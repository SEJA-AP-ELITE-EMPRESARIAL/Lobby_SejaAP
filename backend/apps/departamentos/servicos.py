"""
A sincronização com o Omie e a leitura que o front consome.

A VERSÃO DA LISTA

O Lobby fica aberto horas na mesma janela. O front reconsulta a lista de tempos
em tempos e compara `versao` com a que tem: é assim que ele descobre que um
departamento entrou, saiu ou mudou de nome sem recarregar a página — o que
apagaria a venda em andamento, que só existe no estado do React.

A versão é um hash do que o consultor VÊ (código, nome e posição dos ativos), e
não da hora da sincronização. Sincronizar de hora em hora sem mudança nenhuma não
pode fazer toda tela aberta achar que a lista mudou.

O DEPARTAMENTO FIXO (TSK-585)

Quando a diretoria fixa um departamento no `/admin`, quem aplica é ESTE arquivo,
e não o `index.html`: a rota pública passa a devolver só o fixo, com
`modo: "fixo"`. O lobby novo lê o modo e trava o campo. Um lobby com o HTML
antigo, aberto desde antes do deploy, ignora o modo e mostra uma lista de um item
só, e continua vendendo certo. Se a regra morasse no front, essa tela seguiria
oferecendo a lista inteira.

A versão também muda quando o modo muda, e é por ela que a tela aberta percebe
que a diretoria fixou ou soltou o campo.
"""
import hashlib
import json
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import ConfiguracaoDepartamento, Departamento
from .omie import DepartamentoOmie


class ListagemVazia(Exception):
    """O Omie respondeu, mas sem departamento nenhum."""


class ConfiguracaoInvalida(Exception):
    """Recusa de gravação da aba Departamentos. A mensagem vai crua para a tela."""


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
        partes = [f"{self.ativos} ativos"]
        for rotulo, itens in (
            ("novos", self.novos),
            ("renomeados", self.renomeados),
            ("desativados", self.desativados),
            ("reativados", self.reativados),
        ):
            if itens:
                partes.append(f"{rotulo}: {', '.join(itens)}")
        if not self.mudou:
            partes.append("sem mudança")
        return "; ".join(partes)


def sincronizar(listados: list[DepartamentoOmie], *, agora=None) -> Resultado:
    """Aplica a listagem do Omie ao banco, numa transação.

    Quem o Omie lista é criado ou atualizado; quem ele deixou de listar é
    desativado (nunca apagado — ver `models.py`).
    """
    if not listados:
        # Não é "todos foram removidos": é o Omie respondendo vazio, que na
        # prática é falha. Desativar tudo aqui tiraria o dropdown de todo
        # consultor por causa de uma resposta ruim.
        raise ListagemVazia(
            "O Omie não listou departamento nenhum; a lista do banco ficou como estava."
        )

    agora = agora or timezone.now()
    resultado = Resultado()
    do_omie = {d.codigo: d for d in listados}

    with transaction.atomic():
        existentes = {d.codigo: d for d in Departamento.objects.select_for_update()}

        for codigo, item in do_omie.items():
            atual = existentes.get(codigo)
            if atual is None:
                Departamento.objects.create(
                    codigo=codigo,
                    descricao=item.descricao,
                    estrutura=item.estrutura,
                    ativo=item.ativo,
                    visto_em=agora,
                )
                # Nascer inativo não muda nada na tela do consultor.
                if item.ativo:
                    resultado.novos.append(item.descricao)
                continue

            if item.ativo and not atual.ativo:
                resultado.reativados.append(item.descricao)
            elif atual.ativo and not item.ativo:
                resultado.desativados.append(atual.descricao)
            elif item.ativo and atual.descricao != item.descricao:
                resultado.renomeados.append(f"{atual.descricao} → {item.descricao}")

            novo = {"descricao": item.descricao, "estrutura": item.estrutura, "ativo": item.ativo}
            campos = ["visto_em"]
            if any(getattr(atual, nome) != valor for nome, valor in novo.items()):
                for nome, valor in novo.items():
                    setattr(atual, nome, valor)
                # `alterado_em` só anda quando algo mudou de verdade; o
                # `visto_em` anda em toda rodada.
                campos += [*novo, "alterado_em"]
            atual.visto_em = agora
            atual.save(update_fields=campos)

        for codigo, atual in existentes.items():
            if codigo not in do_omie and atual.ativo:
                # `visto_em` fica onde estava: é a última vez que o Omie o listou.
                atual.ativo = False
                atual.save(update_fields=["ativo", "alterado_em"])
                resultado.desativados.append(f"{atual.descricao} (saiu da listagem)")

    resultado.ativos = Departamento.objects.filter(ativo=True).count()
    return resultado


def ativos():
    return Departamento.objects.filter(ativo=True).order_by("estrutura", "descricao", "codigo")


def versao(departamentos, modo=ConfiguracaoDepartamento.Modo.LISTA) -> str:
    chave = [[d.codigo, d.descricao, d.estrutura] for d in departamentos]
    if modo == ConfiguracaoDepartamento.Modo.FIXO:
        # Só o fixo entra marcado. A lista fica com o mesmo hash de antes da
        # TSK-585: sem isso, o deploy faria toda tela aberta achar que a lista
        # mudou. E a marca não é enfeite: fixo num departamento que é o único
        # ativo daria a mesma lista, e o lobby não saberia que tem de travar.
        chave = ["fixo", chave]
    return hashlib.sha256(json.dumps(chave, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def ultima_sincronizacao():
    """Momento da última rodada bem-sucedida, ou None se nunca houve.

    Toda rodada que dá certo carimba `visto_em` em tudo que o Omie listou, então
    o maior `visto_em` É a última rodada boa. Rodada que falha não carimba nada.
    """
    return Departamento.objects.aggregate(ultima=Max("visto_em"))["ultima"]


def _publico(d: Departamento) -> dict:
    return {"codigo": d.codigo, "descricao": d.descricao, "estrutura": d.estrutura}


def configuracao_atual() -> ConfiguracaoDepartamento | None:
    """A última gravação da aba, ou None se a diretoria nunca a salvou."""
    return ConfiguracaoDepartamento.objects.select_related("departamento").first()


def fixo_em_vigor(configuracao=None) -> Departamento | None:
    """O departamento que o consultor vê travado, ou None quando vale a lista.

    Fixo inativo no Omie conta como lista: ver `ConfiguracaoDepartamento`.
    """
    configuracao = configuracao if configuracao is not None else configuracao_atual()
    if configuracao is None or configuracao.modo != ConfiguracaoDepartamento.Modo.FIXO:
        return None
    departamento = configuracao.departamento
    return departamento if departamento is not None and departamento.ativo else None


def estado_publico() -> dict:
    fixo = fixo_em_vigor()
    if fixo is not None:
        modo, lista = ConfiguracaoDepartamento.Modo.FIXO, [fixo]
    else:
        modo, lista = ConfiguracaoDepartamento.Modo.LISTA, list(ativos())
    ultima = ultima_sincronizacao()
    return {
        "versao": versao(lista, modo),
        "sincronizado_em": ultima.isoformat() if ultima else None,
        # Com "fixo", `departamentos` tem um item só, e é ele que vai na venda.
        "modo": str(modo),
        "departamentos": [_publico(d) for d in lista],
    }


def estado_configuracao() -> dict:
    """O que a aba Departamentos do `/admin` precisa para abrir.

    `modo` é o que a diretoria salvou; `modo_em_vigor` é o que o consultor vê
    agora. Os dois só divergem quando o fixo foi inativado no Omie, e a aba
    avisa.
    """
    configuracao = configuracao_atual()
    fixo = configuracao.departamento if configuracao is not None else None
    ultima = ultima_sincronizacao()
    return {
        "modo": configuracao.modo if configuracao is not None else ConfiguracaoDepartamento.Modo.LISTA.value,
        "modo_em_vigor": (
            ConfiguracaoDepartamento.Modo.FIXO.value
            if fixo_em_vigor(configuracao) is not None
            else ConfiguracaoDepartamento.Modo.LISTA.value
        ),
        "fixo": {**_publico(fixo), "ativo": fixo.ativo} if fixo is not None else None,
        "departamentos": [_publico(d) for d in ativos()],
        "sincronizado_em": ultima.isoformat() if ultima else None,
        "alterado_em": configuracao.criado_em.isoformat() if configuracao is not None else None,
        # Sem autor numa gravação que existe só se a conta foi apagada; a cópia
        # do e-mail fica. None aqui é "ninguém salvou ainda".
        "alterado_por": (configuracao.autor_email or None) if configuracao is not None else None,
    }


def salvar_configuracao(dados, *, autor) -> ConfiguracaoDepartamento:
    """Grava a escolha da aba como uma linha nova. Nada é editado."""
    if not isinstance(dados, dict):
        raise ConfiguracaoInvalida("Configuração inválida.")

    modo = str(dados.get("modo") or "").strip()
    if modo not in ConfiguracaoDepartamento.Modo.values:
        raise ConfiguracaoInvalida("Escolha entre a lista completa e um departamento fixo.")

    departamento = None
    if modo == ConfiguracaoDepartamento.Modo.FIXO:
        codigo = str(dados.get("codigo") or "").strip()
        if not codigo:
            raise ConfiguracaoInvalida("Escolha qual departamento fica fixo.")
        departamento = Departamento.objects.filter(codigo=codigo).first()
        if departamento is None:
            raise ConfiguracaoInvalida(
                f"O departamento {codigo} não está na lista do Omie. Recarregue a página."
            )
        if not departamento.ativo:
            # A lista da tela pode ser de antes da última sincronização.
            raise ConfiguracaoInvalida(
                f"{departamento.descricao} está inativo no Omie e não pode ficar fixo. "
                "Recarregue a página."
            )

    return ConfiguracaoDepartamento.objects.create(
        modo=modo,
        departamento=departamento,
        autor=autor if getattr(autor, "is_authenticated", False) else None,
        autor_email=getattr(autor, "email", "") or "",
    )
