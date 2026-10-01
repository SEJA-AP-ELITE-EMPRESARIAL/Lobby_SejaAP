"""
A sincronização com a fonte e a leitura que o front consome.

É o `apps/departamentos/servicos.py` com outro nome: a versão é um hash do que o
consultor VÊ, e muda com o modo; quem aplica a categoria fixa é este arquivo, e
não o `index.html`. O porquê de cada regra está lá.

O QUE É SÓ DAQUI: O CAMPO SÓ APARECE DEPOIS DA PRIMEIRA SINCRONIZAÇÃO

O app pode ir para produção antes da API das categorias. Enquanto nenhuma
rodada deu certo, `sincronizado_em` sai null, e o lobby lê isso como "o campo
ainda não existe": não mostra o cartão e a venda segue como antes. Sem essa
regra, o deploy poria um cartão vazio, com aviso de erro, em toda venda da APN.
"""
import hashlib
import json
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .fonte import CategoriaDaFonte
from .models import CategoriaFinanceira, ConfiguracaoCategoriaFinanceira

Modo = ConfiguracaoCategoriaFinanceira.Modo


class ListagemVazia(Exception):
    """A fonte respondeu, mas sem categoria nenhuma."""


class ConfiguracaoInvalida(Exception):
    """Recusa de gravação da aba Categorias. A mensagem vai crua para a tela."""


@dataclass
class Resultado:
    ativos: int = 0
    novos: list[str] = field(default_factory=list)
    renomeados: list[str] = field(default_factory=list)
    desativados: list[str] = field(default_factory=list)
    reativados: list[str] = field(default_factory=list)

    @property
    def mudou(self) -> bool:
        return bool(self.novos or self.renomeados or self.desativados or self.reativados)

    def __str__(self) -> str:
        partes = [f"{self.ativos} ativas"]
        for rotulo, itens in (
            ("novas", self.novos),
            ("renomeadas", self.renomeados),
            ("desativadas", self.desativados),
            ("reativadas", self.reativados),
        ):
            if itens:
                partes.append(f"{rotulo}: {', '.join(itens)}")
        if not self.mudou:
            partes.append("sem mudança")
        return "; ".join(partes)


def sincronizar(listados: list[CategoriaDaFonte], *, agora=None) -> Resultado:
    """Aplica a listagem da fonte ao banco, numa transação.

    Quem a fonte lista é criado ou atualizado; quem ela deixou de listar é
    desativado, nunca apagado.
    """
    if not listados:
        # Resposta vazia é falha, não "todas foram removidas": desativar tudo
        # tiraria o campo de toda venda por causa de uma resposta ruim.
        raise ListagemVazia(
            "A fonte não listou categoria nenhuma; a lista do banco ficou como estava."
        )

    agora = agora or timezone.now()
    resultado = Resultado()
    da_fonte = {c.codigo: c for c in listados}

    with transaction.atomic():
        existentes = {c.codigo: c for c in CategoriaFinanceira.objects.select_for_update()}

        for codigo, item in da_fonte.items():
            atual = existentes.get(codigo)
            if atual is None:
                CategoriaFinanceira.objects.create(
                    codigo=codigo, descricao=item.descricao, ativo=item.ativo, visto_em=agora
                )
                if item.ativo:
                    resultado.novos.append(item.descricao)
                continue

            if item.ativo and not atual.ativo:
                resultado.reativados.append(item.descricao)
            elif atual.ativo and not item.ativo:
                resultado.desativados.append(atual.descricao)
            elif item.ativo and atual.descricao != item.descricao:
                resultado.renomeados.append(f"{atual.descricao} → {item.descricao}")

            novo = {"descricao": item.descricao, "ativo": item.ativo}
            campos = ["visto_em"]
            if any(getattr(atual, nome) != valor for nome, valor in novo.items()):
                for nome, valor in novo.items():
                    setattr(atual, nome, valor)
                campos += [*novo, "alterado_em"]
            atual.visto_em = agora
            atual.save(update_fields=campos)

        for codigo, atual in existentes.items():
            if codigo not in da_fonte and atual.ativo:
                atual.ativo = False
                atual.save(update_fields=["ativo", "alterado_em"])
                resultado.desativados.append(f"{atual.descricao} (saiu da listagem)")

    resultado.ativos = CategoriaFinanceira.objects.filter(ativo=True).count()
    return resultado


def ativas():
    return CategoriaFinanceira.objects.filter(ativo=True).order_by("descricao", "codigo")


def versao(categorias, modo=Modo.LISTA) -> str:
    chave = [[c.codigo, c.descricao] for c in categorias]
    if modo == Modo.FIXO:
        # Fixa na única ativa daria a mesma lista; a marca é o que avisa o lobby
        # de que tem de travar o campo.
        chave = ["fixo", chave]
    return hashlib.sha256(json.dumps(chave, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def ultima_sincronizacao():
    """Momento da última rodada bem-sucedida, ou None se nunca houve."""
    return CategoriaFinanceira.objects.aggregate(ultima=Max("visto_em"))["ultima"]


def _publico(c: CategoriaFinanceira) -> dict:
    return {"codigo": c.codigo, "descricao": c.descricao}


def configuracao_atual() -> ConfiguracaoCategoriaFinanceira | None:
    """A última gravação da aba, ou None se a diretoria nunca a salvou."""
    return ConfiguracaoCategoriaFinanceira.objects.select_related("categoria").first()


def fixa_em_vigor(configuracao=None) -> CategoriaFinanceira | None:
    """A categoria que o consultor vê travada, ou None quando vale a lista.

    Fixa inativa na fonte conta como lista.
    """
    configuracao = configuracao if configuracao is not None else configuracao_atual()
    if configuracao is None or configuracao.modo != Modo.FIXO:
        return None
    categoria = configuracao.categoria
    return categoria if categoria is not None and categoria.ativo else None


def estado_publico() -> dict:
    fixa = fixa_em_vigor()
    if fixa is not None:
        modo, lista = Modo.FIXO, [fixa]
    else:
        modo, lista = Modo.LISTA, list(ativas())
    ultima = ultima_sincronizacao()
    return {
        "versao": versao(lista, modo),
        # null = nenhuma rodada deu certo ainda, e o lobby esconde o campo.
        "sincronizado_em": ultima.isoformat() if ultima else None,
        # Com "fixo", `categorias` tem um item só, e é ele que vai na venda.
        "modo": str(modo),
        "categorias": [_publico(c) for c in lista],
    }


def estado_configuracao() -> dict:
    """O que a aba Categorias do `/admin` precisa para abrir.

    `modo` é o que a diretoria salvou; `modo_em_vigor` é o que o consultor vê.
    Só divergem quando a fixa foi inativada na fonte.
    """
    configuracao = configuracao_atual()
    fixa = configuracao.categoria if configuracao is not None else None
    ultima = ultima_sincronizacao()
    return {
        "modo": configuracao.modo if configuracao is not None else Modo.LISTA.value,
        "modo_em_vigor": Modo.FIXO.value if fixa_em_vigor(configuracao) is not None else Modo.LISTA.value,
        "fixa": {**_publico(fixa), "ativo": fixa.ativo} if fixa is not None else None,
        "categorias": [_publico(c) for c in ativas()],
        "sincronizado_em": ultima.isoformat() if ultima else None,
        "alterado_em": configuracao.criado_em.isoformat() if configuracao is not None else None,
        "alterado_por": (configuracao.autor_email or None) if configuracao is not None else None,
    }


def salvar_configuracao(dados, *, autor) -> ConfiguracaoCategoriaFinanceira:
    """Grava a escolha da aba como uma linha nova. Nada é editado."""
    if not isinstance(dados, dict):
        raise ConfiguracaoInvalida("Configuração inválida.")

    modo = str(dados.get("modo") or "").strip()
    if modo not in Modo.values:
        raise ConfiguracaoInvalida("Escolha entre a lista completa e uma categoria fixa.")

    categoria = None
    if modo == Modo.FIXO:
        codigo = str(dados.get("codigo") or "").strip()
        if not codigo:
            raise ConfiguracaoInvalida("Escolha qual categoria fica fixa.")
        categoria = CategoriaFinanceira.objects.filter(codigo=codigo).first()
        if categoria is None:
            raise ConfiguracaoInvalida(
                f"A categoria {codigo} não está na lista sincronizada. Recarregue a página."
            )
        if not categoria.ativo:
            raise ConfiguracaoInvalida(
                f"{categoria.descricao} está inativa e não pode ficar fixa. Recarregue a página."
            )

    return ConfiguracaoCategoriaFinanceira.objects.create(
        modo=modo,
        categoria=categoria,
        autor=autor if getattr(autor, "is_authenticated", False) else None,
        autor_email=getattr(autor, "email", "") or "",
    )
