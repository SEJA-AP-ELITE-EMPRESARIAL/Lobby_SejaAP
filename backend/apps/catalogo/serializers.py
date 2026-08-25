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


def serializa_politica(politica) -> dict:
    """A regra de data do cronograma, do jeito que o front a consome.

    Chaves em português, ao contrário do resto deste arquivo. É deliberado: o
    catálogo herdou nomes em inglês do KV e mudá-los quebraria o `index.html` em
    produção; a política nasce agora, e não há motivo para inventar um segundo
    idioma. O front lê `cobranca.dia_vencimento` direto.
    """
    return politica.como_dicionario()


def serializa_produto(produto, politica=None) -> dict:
    """Espelha o produto normalizado do KV (`functions/_lib/catalogo.js:146-152`,
    removido da árvore — `git show 338e932`; ver a nota em `models.py`).

    `monthly`, `recurring` e `vigencia` só aparecem em produto recorrente — é
    assim que o front distingue os dois (`recurring` ausente = avulso), e é a
    forma que o KV emitia.

    `cobranca` segue a mesma lógica: só sai quando o produto TEM exceção. Produto
    que herda a política geral fica byte a byte igual ao que o front recebe hoje,
    e o resolvedor do lado de lá é um `||` — `produto.cobranca || cobrancaGeral`.

    PRODUTO DE FLUXO PRÓPRIO sai com `flow` e SEM `price`. A ausência é
    deliberada: `price: 0` seria um preço — e um preço errado, que o front
    formataria como "R$ 0,00" na lista e mandaria ao n8n como valor da venda.
    Sem a chave, quem lê é obrigado a olhar o `flow` e ir buscar os valores onde
    eles estão, que é o formulário.

    O outro lado disso: um `index.html` antigo, que não conhece o fluxo, mostra
    esse produto sem valor e não sabe abri-lo. É por isso que criar produto de
    formulário é deploy do front junto — ver a migration `0010`.
    """
    dados = {
        "id": produto.slug,
        "name": produto.nome,
        "sigla": produto.sigla,
        "desc": produto.descricao,
        "duration": produto.duracao,
        "icon": produto.icone,
    }
    if produto.fluxo:
        dados["flow"] = produto.fluxo
    else:
        dados["price"] = numero(produto.preco)
    if produto.recorrente:
        dados["monthly"] = numero(produto.mensalidade)
        dados["recurring"] = True
        dados["vigencia"] = produto.vigencia_meses

    if politica is None:
        # `politica_cobranca` é OneToOne reverso: sem exceção, acessar levanta
        # RelatedObjectDoesNotExist em vez de devolver None.
        politica = getattr(produto, "politica_cobranca", None)
    if politica is not None:
        dados["cobranca"] = serializa_politica(politica)
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
    # Só sai quando existe: categoria sem referência mantém o contrato de hoje,
    # e o front distingue "não configurado" de "configurado como zero".
    if categoria.valor_referencia is not None:
        dados["valor_referencia"] = numero(categoria.valor_referencia)
    dados["products"] = [serializa_produto(p) for p in produtos]
    return dados


def serializa_catalogo(categorias) -> list[dict]:
    """A lista completa, na ordem em que os cards aparecem no lobby.

    A ordem importa: o front faz `CATS.map` direto, sem ordenar
    (`index.html:1284`). No KV ela era a ordem do array; aqui é `Categoria.ordem`.

    Quem chama precisa ter feito `prefetch_related("produtos__politica_cobranca")`
    — ver `CatalogoView._cats`. Sem isso, cada produto vira uma consulta a mais
    procurando uma exceção que quase nunca existe.
    """
    return [
        serializa_categoria(cat, list(cat.produtos.all()))
        for cat in categorias
    ]
