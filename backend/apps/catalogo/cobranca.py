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
"""
from django.db import transaction
from django.utils import timezone

from .models import (
    DIA_VENCIMENTO_MAX,
    DIA_VENCIMENTO_MIN,
    ENTRADA_PRAZO_MAX,
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


def _regra_validada(bruto: dict, rotulo: str) -> dict:
    """Os três campos de uma política, conferidos. Nada é gravado aqui."""
    if not isinstance(bruto, dict):
        raise CobrancaInvalida(f"Configuração inválida em {rotulo}.")

    primeiro = str(bruto.get("primeiro_vencimento") or "").strip()
    if primeiro not in PrimeiroVencimento.values:
        raise CobrancaInvalida(
            f"Regra da primeira parcela desconhecida em {rotulo}: {primeiro!r}."
        )

    return {
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


def _aplica(politica: PoliticaCobranca, regra: dict) -> bool:
    """Escreve a regra na política. Devolve se alguma coisa mudou de fato."""
    mudou = any(getattr(politica, campo) != valor for campo, valor in regra.items())
    if mudou:
        for campo, valor in regra.items():
            setattr(politica, campo, valor)
        politica.save(update_fields=[*regra, "atualizado_em"])
    return mudou


@transaction.atomic
def publica_politica(dados, *, autor=None) -> dict:
    """Aplica a política geral e a lista de exceções. Devolve o estado publicado.

    Levanta `CobrancaInvalida` sem ter tocado no banco — a validação inteira
    acontece antes da primeira escrita, e a transação cobre o resto. Publicação
    pela metade aqui significaria produto cobrando num dia e o resto em outro.
    """
    if not isinstance(dados, dict):
        raise CobrancaInvalida("Corpo inválido: esperado um objeto com `geral`.")

    regra_geral = _regra_validada(dados.get("geral"), "na política geral")

    brutas = dados.get("excecoes") or []
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
        excecoes.append((produto, _regra_validada(bruta, f'em "{produto.nome}"')))

    agora = timezone.now()

    geral = PoliticaCobranca.geral_atual()
    if _aplica(geral, regra_geral):
        registrar_politica(geral, autor=autor, quando=agora)

    mantidos = set()
    for produto, regra in excecoes:
        politica, criada = PoliticaCobranca.objects.get_or_create(
            produto=produto, defaults={"geral": False, **regra}
        )
        mantidos.add(produto.pk)
        if criada or _aplica(politica, regra):
            registrar_politica(politica, autor=autor, quando=agora)

    # Exceções que a tela não mandou de volta deixaram de existir. A linha do
    # tempo fecha o período delas — sem isso, a trilha diria que a exceção vale
    # até hoje, e o consultor estaria vendo outra data.
    removidas = PoliticaCobranca.objects.filter(geral=False).exclude(
        produto__in=mantidos
    ).select_related("produto")
    for politica in removidas:
        encerrar(politica.produto.slug, autor=autor, quando=agora)
    removidas.delete()

    return estado_atual()


def estado_atual() -> dict:
    """A política geral e as exceções, no formato que a tela e o front leem."""
    geral = PoliticaCobranca.geral_atual()
    excecoes = (
        PoliticaCobranca.objects.filter(geral=False)
        .select_related("produto", "produto__categoria")
        .order_by("produto__categoria__ordem", "produto__ordem", "produto_id")
    )
    return {
        "geral": geral.como_dicionario(),
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


__all__ = ["CobrancaInvalida", "estado_atual", "publica_politica"]
