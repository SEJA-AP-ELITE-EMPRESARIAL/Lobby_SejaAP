"""
Publicação da política de cobrança — as datas com que o cronograma nasce.

O IRMÃO DE `servicos.py`, COM UMA DIFERENÇA QUE IMPORTA

Publicar preço **nunca cria nem apaga linha** (ver o cabeçalho de `servicos.py`):
produto que some do catálogo deixa protocolo de venda órfão. Aqui é diferente, e
de propósito — a exceção de um produto é criada e removida por aqui.

O que justifica a diferença: exceção de cobrança não é referenciada por venda
nenhuma. Removê-la não apaga história (a linha do tempo registra até quando ela
valeu) nem deixa registro órfão; só devolve o produto à política geral, que é
exatamente o que "remover a exceção" quer dizer. Não haveria outro lugar para
fazer isso.

O corpo é a lista COMPLETA de exceções, como a tela mostra. Exceção que não vem
no corpo é removida — pela mesma razão que o `/admin` manda o catálogo inteiro
no PUT: a tela é a fonte, e ela sempre tem o conjunto todo na mão.

POR CATEGORIA (TSK-1059, ADR-002 §13.5)

Desde 15/10/2026 cada categoria tem a sua regra. Dois corpos são aceitos:

    {"categorias": {"elite": {...}, "apn": {...}}, "excecoes": [...]}
        O do Admin Lobby do Conecta. Categoria ausente no corpo não muda;
        `excecoes` ausente também não muda — presente, é a lista completa.

    {"geral": {...}, "excecoes": [...]}
        O do /admin de hoje (até a TSK-1062). A tela dele fala de UMA regra para
        o Lobby inteiro, e o backend faz o que ela diz: aplica a regra à geral e a
        TODAS as categorias. Foi a escolha mais segura para o /admin antigo
        continuar funcionando sem mudar de semântica — ver o relatório da TSK.

Os dois juntos são recusados: não há como saber qual a pessoa quis.

O DIA FIXO (TSK-1077)

Cada regra (de categoria e de exceção) leva `dia_fixo`, true ou false. Ele é
OPCIONAL no corpo novo: ausente, a regra que já existe fica com o que tem, e a
que nasce agora nasce ligada. No corpo do /admin antigo ele é IGNORADO, mesmo
que venha: aquela tela não mostra o campo (devolve o que leu, e uma leitura
velha desfaria o que o Conecta gravou depois), e a regra única dela não pode
religar o dia fixo de uma categoria que o Conecta desligou.
"""
from django.db import transaction
from django.utils import timezone

from .models import (
    DIA_VENCIMENTO_MAX,
    DIA_VENCIMENTO_MIN,
    ENTRADA_PRAZO_MAX,
    Categoria,
    PoliticaCobranca,
    PrimeiroVencimento,
    Produto,
)
from .vigencias import encerrar, registrar_politica


class CobrancaInvalida(Exception):
    """Recusa de publicação. A mensagem vai crua para a tela da diretoria."""


def _inteiro(bruto, campo, minimo, maximo, rotulo) -> int:
    try:
        valor = int(str(bruto).strip())
    except (TypeError, ValueError):
        raise CobrancaInvalida(f"{campo} inválido em {rotulo}.")
    if not minimo <= valor <= maximo:
        raise CobrancaInvalida(
            f"{campo} em {rotulo}: use de {minimo} a {maximo}."
        )
    return valor


def _regra_validada(bruto: dict, rotulo: str, *, com_dia_fixo=True) -> dict:
    """Os campos de uma política, conferidos. Nada é gravado aqui.

    `dia_fixo` só entra na regra quando veio (e `com_dia_fixo`): é o que faz a
    gravação manter o valor guardado quando ele não vem. Desligado, o dia do
    vencimento e a primeira parcela continuam obrigatórios — ficam guardados
    para quando alguém religar.
    """
    if not isinstance(bruto, dict):
        raise CobrancaInvalida(f"Configuração inválida em {rotulo}.")

    primeiro = str(bruto.get("primeiro_vencimento") or "").strip()
    if primeiro not in PrimeiroVencimento.values:
        raise CobrancaInvalida(
            f"Regra da primeira parcela desconhecida em {rotulo}: {primeiro!r}."
        )

    regra = {
        "dia_vencimento": _inteiro(
            bruto.get("dia_vencimento"),
            "Dia do vencimento",
            DIA_VENCIMENTO_MIN,
            DIA_VENCIMENTO_MAX,
            rotulo,
        ),
        "primeiro_vencimento": primeiro,
        "entrada_prazo_dias": _inteiro(
            bruto.get("entrada_prazo_dias") or 0,
            "Prazo da entrada",
            0,
            ENTRADA_PRAZO_MAX,
            rotulo,
        ),
    }
    if com_dia_fixo and "dia_fixo" in bruto:
        # Só booleano de verdade. `"false"` (texto) é verdadeiro em Python, e um
        # `null` por engano não pode ligar nem desligar nada em silêncio.
        if not isinstance(bruto["dia_fixo"], bool):
            raise CobrancaInvalida(
                f"Dia do vencimento fixo em {rotulo}: use true ou false."
            )
        regra["dia_fixo"] = bruto["dia_fixo"]
    return regra


def _aplica(politica: PoliticaCobranca, regra: dict) -> bool:
    """Escreve a regra na política. Devolve se alguma coisa mudou de fato."""
    mudou = any(getattr(politica, campo) != valor for campo, valor in regra.items())
    if mudou:
        for campo, valor in regra.items():
            setattr(politica, campo, valor)
        politica.save(update_fields=[*regra, "atualizado_em"])
    return mudou


def _excecoes_validadas(brutas, *, com_dia_fixo=True) -> list:
    if not isinstance(brutas, list):
        raise CobrancaInvalida("`excecoes` precisa ser uma lista.")

    # Valida TODAS as exceções antes de escrever qualquer uma.
    excecoes = []
    vistos = set()
    for bruta in brutas:
        if not isinstance(bruta, dict):
            raise CobrancaInvalida("Exceção inválida na lista.")
        categoria_slug = str(bruta.get("categoria_id") or "").strip()
        produto_slug = str(bruta.get("produto_id") or "").strip()
        if not categoria_slug or not produto_slug:
            raise CobrancaInvalida("Exceção sem categoria ou produto.")

        chave = (categoria_slug, produto_slug)
        if chave in vistos:
            raise CobrancaInvalida(f'Produto "{produto_slug}" repetido nas exceções.')
        vistos.add(chave)

        try:
            produto = Produto.objects.select_related("categoria").get(
                categoria__slug=categoria_slug, slug=produto_slug
            )
        except Produto.DoesNotExist:
            raise CobrancaInvalida(
                f'Produto "{produto_slug}" não existe em "{categoria_slug}". '
                "Recarregue a página."
            )
        excecoes.append(
            (produto, _regra_validada(bruta, f'em "{produto.nome}"', com_dia_fixo=com_dia_fixo))
        )
    # Mesma ordem canônica das categorias, pelo mesmo motivo.
    return sorted(excecoes, key=lambda par: par[0].pk)


def _categorias_validadas(brutas) -> list:
    if not isinstance(brutas, dict):
        raise CobrancaInvalida("`categorias` precisa ser um objeto: {\"elite\": {...}, ...}.")
    existentes = {c.slug: c for c in Categoria.objects.all()}
    validadas = []
    for slug, bruta in brutas.items():
        categoria = existentes.get(str(slug))
        if categoria is None:
            raise CobrancaInvalida(f'Categoria "{slug}" não existe. Recarregue a página.')
        validadas.append((categoria, _regra_validada(bruta, f'em "{categoria.nome}"')))
    # Ordem canônica (o id), e não a do JSON: duas publicações simultâneas com as
    # mesmas categorias em ordens inversas travariam uma à espera da outra
    # (deadlock no Postgres, 500 para as duas).
    return sorted(validadas, key=lambda par: par[0].pk)


def _grava_da_categoria(categoria, regra, *, autor, quando):
    politica, criada = PoliticaCobranca.objects.get_or_create(
        categoria=categoria, defaults={"geral": False, **regra}
    )
    if criada or _aplica(politica, regra):
        registrar_politica(politica, autor=autor, quando=quando)


@transaction.atomic
def publica_politica(dados, *, autor=None) -> dict:
    """Aplica as regras e a lista de exceções. Devolve o estado publicado.

    Levanta `CobrancaInvalida` sem ter tocado no banco — a validação inteira
    acontece antes da primeira escrita, e a transação cobre o resto. Publicação
    pela metade aqui significaria produto cobrando num dia e o resto em outro.
    """
    if not isinstance(dados, dict):
        raise CobrancaInvalida("Corpo inválido: esperado um objeto com `categorias`.")
    if "categorias" in dados and "geral" in dados:
        raise CobrancaInvalida(
            "Mande `categorias` (a regra de cada categoria) ou `geral` (uma regra "
            "para todas), não os dois."
        )

    por_categoria = "categorias" in dados
    if por_categoria:
        categorias = _categorias_validadas(dados.get("categorias"))
        regra_geral = None
    else:
        # O corpo do /admin antigo: uma regra para o Lobby inteiro, sem o dia
        # fixo (ver o cabeçalho).
        regra_geral = _regra_validada(dados.get("geral"), "na política geral", com_dia_fixo=False)
        categorias = [(c, regra_geral) for c in Categoria.objects.order_by("pk")]

    if por_categoria and "excecoes" not in dados:
        excecoes = None  # corpo novo sem a chave: as exceções ficam como estão
    elif por_categoria and not isinstance(dados.get("excecoes"), list):
        # `null` não é "nenhuma exceção": no corpo novo, quem quer apagar todas
        # manda `[]`. Um `null` por engano não pode apagar as exceções em silêncio.
        raise CobrancaInvalida("`excecoes` precisa ser uma lista (ou ficar de fora para não mudar).")
    else:
        excecoes = _excecoes_validadas(dados.get("excecoes") or [], com_dia_fixo=por_categoria)

    agora = timezone.now()

    if regra_geral is not None:
        geral = PoliticaCobranca.geral_atual()
        if _aplica(geral, regra_geral):
            registrar_politica(geral, autor=autor, quando=agora)

    for categoria, regra in categorias:
        _grava_da_categoria(categoria, regra, autor=autor, quando=agora)

    if excecoes is None:
        return estado_atual()

    mantidos = set()
    for produto, regra in excecoes:
        politica, criada = PoliticaCobranca.objects.get_or_create(
            produto=produto, defaults={"geral": False, **regra}
        )
        mantidos.add(produto.pk)
        if criada or _aplica(politica, regra):
            registrar_politica(politica, autor=autor, quando=agora)

    # Exceções que a tela não mandou de volta deixaram de existir. A linha do
    # tempo fecha o período delas, com QUEM removeu — sem isso, a trilha diria
    # que a exceção vale até hoje, e o consultor estaria vendo outra data.
    removidas = PoliticaCobranca.objects.filter(produto__isnull=False).exclude(
        produto__in=mantidos
    ).select_related("produto")
    for politica in removidas:
        encerrar(politica.produto.slug, autor=autor, quando=agora)
    removidas.delete()

    return estado_atual()


def regra_da_categoria(categoria, geral=None) -> dict:
    """A regra que vale para os produtos sem exceção de uma categoria.

    A dela, se existe; senão a geral, que é a regra com que toda categoria
    começa (inclusive uma criada depois da TSK-1059 no /django-admin/).
    Quem chama em laço passa a `geral` e um queryset com
    `select_related("politica_cobranca")`, para não consultar uma vez por
    categoria.
    """
    politica = getattr(categoria, "politica_cobranca", None)
    if politica is not None:
        return politica.como_dicionario()
    return (geral or PoliticaCobranca.geral_atual()).como_dicionario()


# O que o corpo do /admin antigo (`geral`) aplica a todas as categorias.
CAMPOS_DA_REGRA_UNICA = ("dia_vencimento", "primeiro_vencimento", "entrada_prazo_dias")


def _da_regra_unica(regra: dict) -> dict:
    return {campo: regra[campo] for campo in CAMPOS_DA_REGRA_UNICA}


def estado_atual() -> dict:
    """As regras de cada categoria e as exceções, no formato que as telas leem."""
    geral = PoliticaCobranca.geral_atual()
    categorias = Categoria.objects.select_related("politica_cobranca").order_by("ordem", "id")
    por_categoria = {
        c.slug: {"nome": c.nome, **regra_da_categoria(c, geral)} for c in categorias
    }
    excecoes = (
        PoliticaCobranca.objects.filter(produto__isnull=False)
        .select_related("produto", "produto__categoria")
        .order_by("produto__categoria__ordem", "produto__ordem", "produto_id")
    )
    regra_geral = geral.como_dicionario()
    return {
        "categorias": por_categoria,
        # Para o /admin de hoje, que só conhece uma regra. `categorias_divergem`
        # é o aviso de que ela não conta a história toda: alguma categoria está
        # com regra própria, diferente desta, e salvar lá a sobrescreve. Conta
        # só os campos que salvar lá sobrescreve — o `dia_fixo` não é um deles.
        "geral": regra_geral,
        "categorias_divergem": any(
            _da_regra_unica(regra) != _da_regra_unica(regra_geral)
            for regra in por_categoria.values()
        ),
        "excecoes": [
            {
                "categoria_id": p.produto.categoria.slug,
                "produto_id": p.produto.slug,
                "produto_nome": p.produto.nome,
                **p.como_dicionario(),
            }
            for p in excecoes
        ],
        "opcoes": {
            "primeiro_vencimento": [
                {"valor": valor, "rotulo": rotulo}
                for valor, rotulo in PrimeiroVencimento.choices
            ],
            "dia_vencimento": {"min": DIA_VENCIMENTO_MIN, "max": DIA_VENCIMENTO_MAX},
            "entrada_prazo_dias": {"min": 0, "max": ENTRADA_PRAZO_MAX},
        },
    }


__all__ = ["CobrancaInvalida", "estado_atual", "publica_politica", "regra_da_categoria"]
