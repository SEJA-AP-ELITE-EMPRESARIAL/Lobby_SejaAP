"""
A tradução entre o banco e o JSON que o front consome.

Este arquivo é o contrato. O `index.html` e o `admin.html` são servidos como
arquivos estáticos e NÃO mudam junto com o backend — um campo a menos aqui é
uma quebra em produção, na mão do consultor, sem erro no log.

Por isso a serialização é escrita à mão, campo a campo, em vez de `fields = "__all__"`:
a lista explícita é auditável, e o teste `tests_contrato.py` a compara com o
catálogo literal que está no ar hoje.

Nomes: as colunas são em português (padrão da casa), as chaves do JSON são as
que o front já lê (inglês). O mapeamento vive aqui e em nenhum outro lugar.
"""
from decimal import Decimal

from .models import arredonda


def numero(valor) -> int | float:
    """Emite número JSON de verdade — nunca string.

    O DRF serializa `DecimalField` como `"9997.00"` por padrão, e isso quebra
    duas coisas silenciosamente: o `admin.html:209` compara os valores com `!==`
    estrito para marcar o que foi alterado, e o `index.html:1039` repassa o
    `valor_tabela` cru para o webhook do n8n.

    Devolve `int` quando não há centavos, para o JSON sair idêntico ao de hoje
    (`119964`, não `119964.0`).
    """
    dec = arredonda(valor)
    return int(dec) if dec == dec.to_integral_value() else float(dec)


def serializa_produto(produto) -> dict:
    """Espelha o produto normalizado do KV (`functions/_lib/catalogo.js:146-152`,
    removido da árvore — `git show 338e932`; ver a nota em `models.py`).

    `monthly`, `recurring` e `vigencia` só aparecem em produto recorrente — é
    assim que o front distingue os dois (`recurring` ausente = avulso), e é a
    forma que o KV emitia.
    """
    dados = {
        "id": produto.slug,
        "name": produto.nome,
        "sigla": produto.sigla,
        "desc": produto.descricao,
        "duration": produto.duracao,
        "icon": produto.icone,
        "price": numero(produto.preco),
    }
    if produto.recorrente:
        dados["monthly"] = numero(produto.mensalidade)
        dados["recurring"] = True
        dados["vigencia"] = produto.vigencia_meses
    return dados


def serializa_categoria(categoria, produtos=None) -> dict:
    """Espelha a categoria normalizada do KV (`functions/_lib/catalogo.js:155-162`).

    Duas diferenças deliberadas em relação ao normalizador antigo:

    - `locked` continua saindo só quando verdadeiro (o front testa `!!c.locked`);
    - `flow` e `sigla` AGORA são emitidos. O normalizador do KV os descartava, e
      só a APN os tinha porque vinha de uma constante em código que nunca passava
      por ele. Como a APN virou linha no banco, esses campos precisam sobreviver
      à serialização — sem `flow` o front trata a APN como categoria comum e
      manda o consultor para o wizard errado.
    """
    if produtos is None:
        produtos = categoria.produtos.all()

    dados = {
        "id": categoria.slug,
        "name": categoria.nome,
        "icon": categoria.icone,
        "color": categoria.cor,
        "desc": categoria.descricao,
    }
    if categoria.travada:
        dados["locked"] = True
    if categoria.fluxo:
        dados["flow"] = categoria.fluxo
    if categoria.sigla:
        dados["sigla"] = categoria.sigla
    dados["products"] = [serializa_produto(p) for p in produtos]
    return dados


def serializa_catalogo(categorias) -> list[dict]:
    """A lista completa, na ordem em que os cards aparecem no lobby.

    A ordem importa: o front faz `CATS.map` direto, sem ordenar
    (`index.html:1284`). No KV ela era a ordem do array; aqui é `Categoria.ordem`.
    """
    return [
        serializa_categoria(cat, list(cat.produtos.all()))
        for cat in categorias
    ]
