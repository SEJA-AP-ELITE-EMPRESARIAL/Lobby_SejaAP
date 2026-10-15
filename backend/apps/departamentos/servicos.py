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

POR CATEGORIA (TSK-1059)

Desde 15/10/2026 a escolha é de cada categoria. A rota pública manda, além do
que mandava, `categorias: {slug: {modo, departamentos}}`, e o `index.html` lê a
da categoria da venda. As chaves de sempre (`modo`, `departamentos`) seguem para
um lobby com o HTML de antes: quando todas as categorias estão iguais — o caso
do dia do deploy — elas são exatamente as de antes, com a mesma versão; quando
divergem, viram a lista completa, que nunca trava venda nem manda ao Omie o
departamento de outra categoria.
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


def _categorias():
    from apps.catalogo.models import Categoria

    return list(Categoria.objects.order_by("ordem", "id"))


def configuracoes_atuais() -> dict:
    """A última gravação de cada categoria, por slug. Categoria sem gravação
    fica de fora — e vale a lista completa para ela."""
    atuais = {}
    for configuracao in ConfiguracaoDepartamento.objects.filter(
        categoria__isnull=False
    ).select_related("departamento", "categoria"):
        # A ordenação do modelo é da mais recente para a mais antiga: a primeira
        # de cada categoria é a que vale.
        atuais.setdefault(configuracao.categoria.slug, configuracao)
    return atuais


def configuracao_atual(categoria_slug) -> ConfiguracaoDepartamento | None:
    """A última gravação de uma categoria, ou None se ninguém a salvou."""
    return (
        ConfiguracaoDepartamento.objects.filter(categoria__slug=categoria_slug)
        .select_related("departamento")
        .first()
    )


def fixo_em_vigor(configuracao) -> Departamento | None:
    """O departamento que o consultor vê travado, ou None quando vale a lista.

    Fixo inativo no Omie conta como lista: ver `ConfiguracaoDepartamento`. A
    regra vale para cada categoria separadamente.
    """
    if configuracao is None or configuracao.modo != ConfiguracaoDepartamento.Modo.FIXO:
        return None
    departamento = configuracao.departamento
    return departamento if departamento is not None and departamento.ativo else None


def _efetiva(configuracao) -> tuple:
    """(modo, departamento) gravados, com "sem gravação" valendo a lista."""
    if configuracao is None:
        return (ConfiguracaoDepartamento.Modo.LISTA.value, None)
    return (configuracao.modo, configuracao.departamento_id)


def estado_publico() -> dict:
    lista_completa = list(ativos())
    atuais = configuracoes_atuais()

    por_categoria = {}
    for categoria in _categorias():
        fixo = fixo_em_vigor(atuais.get(categoria.slug))
        if fixo is not None:
            por_categoria[categoria.slug] = (ConfiguracaoDepartamento.Modo.FIXO, [fixo])
        else:
            por_categoria[categoria.slug] = (ConfiguracaoDepartamento.Modo.LISTA, lista_completa)

    vistas = {(modo, tuple(d.codigo for d in lista)) for modo, lista in por_categoria.values()}
    if len(vistas) <= 1:
        # Todas iguais: as chaves de sempre são exatamente as de antes, e a
        # versão também — o deploy não faz tela aberta nenhuma achar que mudou.
        modo, lista = next(iter(por_categoria.values()), (ConfiguracaoDepartamento.Modo.LISTA, lista_completa))
        chave_da_versao = versao(lista, modo)
    else:
        modo, lista = ConfiguracaoDepartamento.Modo.LISTA, lista_completa
        chave_da_versao = hashlib.sha256(
            json.dumps(
                [
                    "categorias",
                    # Código, nome e posição, como em `versao`: renomear um
                    # departamento no Omie tem de mudar a versão, senão a tela
                    # aberta descarta a resposta e segue com o nome velho.
                    sorted(
                        (slug, str(m), [[d.codigo, d.descricao, d.estrutura] for d in l])
                        for slug, (m, l) in por_categoria.items()
                    ),
                ],
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()[:16]

    ultima = ultima_sincronizacao()
    return {
        "versao": chave_da_versao,
        "sincronizado_em": ultima.isoformat() if ultima else None,
        # Com "fixo", `departamentos` tem um item só, e é ele que vai na venda.
        "modo": str(modo),
        "departamentos": [_publico(d) for d in lista],
        "categorias": {
            slug: {"modo": str(m), "departamentos": [_publico(d) for d in l]}
            for slug, (m, l) in por_categoria.items()
        },
    }


def _estado_da_categoria(categoria, configuracao) -> dict:
    fixo = configuracao.departamento if configuracao is not None else None
    return {
        "nome": categoria.nome,
        "modo": _efetiva(configuracao)[0],
        "modo_em_vigor": (
            ConfiguracaoDepartamento.Modo.FIXO.value
            if fixo_em_vigor(configuracao) is not None
            else ConfiguracaoDepartamento.Modo.LISTA.value
        ),
        "fixo": {**_publico(fixo), "ativo": fixo.ativo} if fixo is not None else None,
        "alterado_em": configuracao.criado_em.isoformat() if configuracao is not None else None,
        # Sem autor numa gravação que existe só se a conta foi apagada; a cópia
        # do e-mail fica. None aqui é "ninguém salvou ainda".
        "alterado_por": (configuracao.autor_email or None) if configuracao is not None else None,
    }


def estado_configuracao() -> dict:
    """O que a tela da diretoria precisa para abrir: a escolha de cada categoria.

    `modo` é o que a diretoria salvou; `modo_em_vigor` é o que o consultor vê
    agora. Os dois só divergem quando o fixo foi inativado no Omie, e a tela
    avisa.

    As chaves de fora de `categorias` são do /admin de hoje, que conhece uma
    escolha só: com todas as categorias iguais, são essa escolha; com escolhas
    diferentes, `categorias_divergem` é verdadeiro e elas mostram a lista
    completa — salvar lá aplica a mesma escolha a todas.
    """
    atuais = configuracoes_atuais()
    categorias = _categorias()
    por_categoria = {c.slug: _estado_da_categoria(c, atuais.get(c.slug)) for c in categorias}

    escolhas = {_efetiva(atuais.get(c.slug)) for c in categorias}
    divergem = len(escolhas) > 1
    mais_recente = max(atuais.values(), key=lambda c: (c.criado_em, c.id), default=None)
    if divergem or mais_recente is None:
        legado = {
            "modo": ConfiguracaoDepartamento.Modo.LISTA.value,
            "modo_em_vigor": ConfiguracaoDepartamento.Modo.LISTA.value,
            "fixo": None,
        }
        if mais_recente is not None:
            legado.update(
                alterado_em=mais_recente.criado_em.isoformat(),
                alterado_por=mais_recente.autor_email or None,
            )
        else:
            legado.update(alterado_em=None, alterado_por=None)
    else:
        legado = _estado_da_categoria(mais_recente.categoria, mais_recente)
        legado.pop("nome")

    ultima = ultima_sincronizacao()
    return {
        "categorias": por_categoria,
        "departamentos": [_publico(d) for d in ativos()],
        "sincronizado_em": ultima.isoformat() if ultima else None,
        **legado,
        "categorias_divergem": divergem,
    }


def _escolha_validada(dados, rotulo="") -> tuple:
    """(modo, departamento) conferidos. Nada é gravado aqui."""
    em = f" em {rotulo}" if rotulo else ""
    if not isinstance(dados, dict):
        raise ConfiguracaoInvalida(f"Configuração inválida{em}.")

    modo = str(dados.get("modo") or "").strip()
    if modo not in ConfiguracaoDepartamento.Modo.values:
        raise ConfiguracaoInvalida(f"Escolha entre a lista completa e um departamento fixo{em}.")

    departamento = None
    if modo == ConfiguracaoDepartamento.Modo.FIXO:
        codigo = str(dados.get("codigo") or "").strip()
        if not codigo:
            raise ConfiguracaoInvalida(f"Escolha qual departamento fica fixo{em}.")
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
    return modo, departamento


@transaction.atomic
def salvar_configuracao(dados, *, autor) -> list[ConfiguracaoDepartamento]:
    """Grava a escolha de cada categoria mandada, como linhas novas.

    Dois corpos:

        {"categorias": {"elite": {"modo": "fixo", "codigo": "..."}, "apn": {"modo": "lista"}}}
            O do Admin Lobby do Conecta (ADR-002 §13.5). Categoria ausente no
            corpo não muda.

        {"modo": "fixo", "codigo": "..."}
            O do /admin de hoje, que fala de uma escolha para o Lobby inteiro: vale
            para TODAS as categorias.

    Só ganha linha nova a categoria cuja escolha mudou: mandar o formulário
    inteiro de volta não picota o histórico. A validação inteira acontece antes
    da primeira escrita.
    """
    if not isinstance(dados, dict):
        raise ConfiguracaoInvalida("Configuração inválida.")
    if "categorias" in dados and "modo" in dados:
        raise ConfiguracaoInvalida(
            "Mande `categorias` (a escolha de cada categoria) ou `modo` (uma para "
            "todas), não os dois."
        )

    categorias = {c.slug: c for c in _categorias()}
    if "categorias" in dados:
        brutas = dados.get("categorias")
        if not isinstance(brutas, dict):
            raise ConfiguracaoInvalida("`categorias` precisa ser um objeto: {\"elite\": {...}, ...}.")
        escolhas = []
        for slug, bruta in brutas.items():
            categoria = categorias.get(str(slug))
            if categoria is None:
                raise ConfiguracaoInvalida(f'Categoria "{slug}" não existe. Recarregue a página.')
            escolhas.append((categoria, _escolha_validada(bruta, categoria.nome)))
    else:
        escolha = _escolha_validada(dados)
        escolhas = [(categoria, escolha) for categoria in categorias.values()]

    atuais = configuracoes_atuais()
    gravadas = []
    for categoria, (modo, departamento) in escolhas:
        if _efetiva(atuais.get(categoria.slug)) == (modo, getattr(departamento, "pk", None)):
            continue
        gravadas.append(
            ConfiguracaoDepartamento.objects.create(
                categoria=categoria,
                modo=modo,
                departamento=departamento,
                autor=autor if getattr(autor, "is_authenticated", False) else None,
                autor_email=getattr(autor, "email", "") or "",
            )
        )
    return gravadas
