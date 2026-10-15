"""
A publicação do Admin Lobby do Conecta — `POST /api/integracao/catalogo/publicar`.

Substitui, para o Conecta, o `PUT /api/catalogo` e o `PATCH` de produto do
/admin de hoje (ADR-002 §13.4): os valores e os produtos editados na seção
Catálogo vão num clique só, viram UMA `PublicacaoCatalogo`, com a lista de → para,
e entram juntos ou não entram.

    {
      "valores": [
        {"categoria": "elite", "produto": "pro", "monthly": 10497.00},
        {"categoria": "elite", "produto": "avulso", "price": 28000},
        {"categoria": "apn", "valor_referencia": null}
      ],
      "produtos": [
        {"categoria": "elite", "slug": "pro", "nome": "ELITE PRO", "sigla": "PRO",
         "descricao": "...", "duracao": "12 meses", "icone": "workspace_premium"}
      ]
    }

REGRAS, todas conferidas ANTES da primeira escrita:

- produto ou categoria desconhecidos recusam a publicação inteira (400 com
  `codigo`). Ao contrário do PUT antigo, que mandava o catálogo inteiro e por
  isso tolerava o que não editava, aqui só vem o que mudou: um id que não existe
  é tela velha, e publicar o resto por cima seria aplicar metade de uma decisão;
- campo que não existe é recusado, em vez de ignorado: um erro de digitação do
  outro lado não pode virar "publicado, mas sem a mudança";
- produto recorrente recebe `monthly` (e pode mudar a `vigencia`); avulso recebe
  `price`. O total do recorrente continua calculado (mensalidade × vigência);
- produto de formulário (o Recrutamento e Seleção, o DH - Avaliação) não tem
  preço: valor para ele é recusado, e o texto dele se edita normalmente;
- `valor_referencia` só na categoria de fluxo próprio (a APN). `null` limpa.

O que esta rota não faz, como o PUT antigo: criar, apagar ou reordenar produto,
nem mudar slug ou categoria. Criar é o `POST /api/produtos`, só pelo /admin
antigo (fora da lista do Conecta desde a TSK-1077); apagar não existe.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    MENSALIDADE_MAX,
    VALOR_MAX,
    VIGENCIA_MAX,
    VIGENCIA_MIN,
    Categoria,
    Produto,
    arredonda,
    normaliza_sigla,
)
from .registro import alvo_de_produto, descreve, diferencas, fotografia, grava_publicacao
from .vigencias import registrar_categoria, registrar_produto

CAMPOS_DE_VALOR = {"categoria", "produto", "monthly", "vigencia", "price", "valor_referencia"}
CAMPOS_DE_PRODUTO = {"categoria", "slug", "nome", "sigla", "descricao", "duracao", "icone"}
TEXTOS = ("nome", "descricao", "duracao", "icone")


class PublicacaoInvalida(Exception):
    """Recusa da publicação inteira. Vai para o Conecta com `codigo` e a frase."""

    def __init__(self, frase, codigo="DADOS_INVALIDOS"):
        super().__init__(frase)
        self.codigo = codigo


def _dinheiro(bruto, rotulo, teto) -> Decimal:
    if isinstance(bruto, bool) or bruto is None:
        raise PublicacaoInvalida(f"{rotulo}: valor inválido.")
    try:
        valor = arredonda(Decimal(str(bruto).replace(",", ".").strip()))
    except (InvalidOperation, ValueError):
        raise PublicacaoInvalida(f"{rotulo}: valor inválido.")
    # "NaN" passa pelo Decimal e pelo quantize sem erro, e só estoura na
    # comparação abaixo — um 500 em vez de uma recusa.
    if not valor.is_finite():
        raise PublicacaoInvalida(f"{rotulo}: valor inválido.")
    if not (Decimal(0) < valor <= teto):
        raise PublicacaoInvalida(f"{rotulo}: valor fora da faixa permitida.")
    return valor


def _lista(dados, chave):
    bruto = dados.get(chave, [])
    if not isinstance(bruto, list) or not all(isinstance(item, dict) for item in bruto):
        raise PublicacaoInvalida(f"`{chave}` precisa ser uma lista de objetos.")
    return bruto


def _sobra(item, permitidos, onde):
    desconhecidos = sorted(set(item) - permitidos)
    if desconhecidos:
        raise PublicacaoInvalida(
            f"Campo desconhecido em {onde}: {', '.join(desconhecidos)}.", "CAMPO_DESCONHECIDO"
        )


def _valida_texto(produto: Produto):
    """O `full_clean` do modelo, com o erro traduzido para a frase da tela.

    É ele quem confere sigla de 3 letras, sigla única no catálogo inteiro
    (produtos e categorias de fluxo próprio) e o tamanho de cada texto.
    """
    try:
        produto.full_clean()
    except ValidationError as erro:
        campo, mensagens = next(iter(erro.message_dict.items()))
        try:
            rotulo = "" if campo == "__all__" else str(Produto._meta.get_field(campo).verbose_name)
        except FieldDoesNotExist:
            rotulo = ""
        prefixo = f"{rotulo.capitalize()}: " if rotulo else ""
        raise PublicacaoInvalida(f'"{produto.nome}" — {prefixo}{mensagens[0]}')


def publica_edicoes(dados, *, autor):
    """Valida tudo, aplica tudo numa transação e grava UMA publicação.

    Devolve `(publicacao, linhas)`. Levanta `PublicacaoInvalida` sem ter escrito
    nada.
    """
    if not isinstance(dados, dict):
        raise PublicacaoInvalida("Corpo inválido: esperado um objeto com `valores` e `produtos`.")
    _sobra(dados, {"valores", "produtos"}, "o corpo")
    valores = _lista(dados, "valores")
    textos = _lista(dados, "produtos")

    agora = timezone.now()
    with transaction.atomic():
        produtos = {
            (p.categoria.slug, p.slug): p
            for p in Produto.objects.select_related("categoria").select_for_update()
        }
        categorias = {c.slug: c for c in Categoria.objects.select_for_update()}
        antes = {}  # chave do produto -> fotografia de antes de qualquer mudança
        mexidos = {}  # chave do produto -> produto, na ordem em que apareceram
        referencias = []  # (categoria, valor novo)
        vistos_valor, vistos_texto = set(), set()

        def produto_de(slug_cat, slug):
            produto = produtos.get((slug_cat, slug))
            if produto is None:
                raise PublicacaoInvalida(
                    f'Produto "{slug}" não existe em "{slug_cat}". Recarregue a página.',
                    "PRODUTO_DESCONHECIDO",
                )
            antes.setdefault((slug_cat, slug), fotografia(produto))
            mexidos.setdefault((slug_cat, slug), produto)
            return produto

        for item in valores:
            _sobra(item, CAMPOS_DE_VALOR, "valores")
            slug_cat = str(item.get("categoria") or "").strip()
            if slug_cat not in categorias:
                raise PublicacaoInvalida(
                    f'Categoria "{slug_cat}" não existe. Recarregue a página.', "CATEGORIA_DESCONHECIDA"
                )

            if "produto" not in item:
                categoria = categorias[slug_cat]
                if "valor_referencia" not in item or set(item) - {"categoria", "valor_referencia"}:
                    raise PublicacaoInvalida(
                        f'Item de "{slug_cat}" sem produto: só `valor_referencia` vale aqui.'
                    )
                if not categoria.fluxo:
                    raise PublicacaoInvalida(
                        f'"{categoria.nome}" não tem valor de referência: ele é só da categoria '
                        "de fluxo próprio (a APN)."
                    )
                if ("categoria", slug_cat) in vistos_valor:
                    raise PublicacaoInvalida(f'"{categoria.nome}" repetida em `valores`.')
                vistos_valor.add(("categoria", slug_cat))
                bruto = item["valor_referencia"]
                novo = None if bruto in (None, "") else _dinheiro(
                    bruto, f"{categoria.nome} (valor de referência)", VALOR_MAX
                )
                referencias.append((categoria, novo))
                continue

            if "valor_referencia" in item:
                raise PublicacaoInvalida("`valor_referencia` é da categoria: mande sem `produto`.")
            slug = str(item.get("produto") or "").strip()
            produto = produto_de(slug_cat, slug)
            if (slug_cat, slug) in vistos_valor:
                raise PublicacaoInvalida(f'"{produto.nome}" repetido em `valores`.')
            vistos_valor.add((slug_cat, slug))
            if produto.de_formulario:
                raise PublicacaoInvalida(
                    f'"{produto.nome}" não tem preço de tabela: os valores dele saem do formulário.',
                    "PRODUTO_SEM_PRECO",
                )
            if produto.recorrente:
                if "price" in item or "monthly" not in item:
                    raise PublicacaoInvalida(f'"{produto.nome}" é recorrente: mande `monthly`, não `price`.')
                if "vigencia" in item:
                    bruta = item["vigencia"]
                    if isinstance(bruta, bool) or not isinstance(bruta, int) or not VIGENCIA_MIN <= bruta <= VIGENCIA_MAX:
                        raise PublicacaoInvalida(
                            f'Vigência inválida em "{produto.nome}": use de {VIGENCIA_MIN} a {VIGENCIA_MAX} meses.'
                        )
                    produto.vigencia_meses = bruta
                produto.mensalidade = _dinheiro(item["monthly"], f"Mensalidade de {produto.nome}", MENSALIDADE_MAX)
                if arredonda(produto.mensalidade * produto.vigencia_meses) > VALOR_MAX:
                    raise PublicacaoInvalida(f'Valor total de "{produto.nome}" fora da faixa permitida.')
            else:
                if "monthly" in item or "vigencia" in item or "price" not in item:
                    raise PublicacaoInvalida(f'"{produto.nome}" é avulso: mande `price`, não `monthly`.')
                produto.valor = _dinheiro(item["price"], f"Valor de {produto.nome}", VALOR_MAX)

        for item in textos:
            _sobra(item, CAMPOS_DE_PRODUTO, "produtos")
            slug_cat = str(item.get("categoria") or "").strip()
            slug = str(item.get("slug") or "").strip()
            produto = produto_de(slug_cat, slug)
            if (slug_cat, slug) in vistos_texto:
                raise PublicacaoInvalida(f'"{produto.nome}" repetido em `produtos`.')
            vistos_texto.add((slug_cat, slug))
            for campo in TEXTOS:
                if campo in item:
                    setattr(produto, campo, str(item.get(campo) or "").strip())
            if "sigla" in item:
                produto.sigla = normaliza_sigla(item.get("sigla"))
            if not produto.nome:
                raise PublicacaoInvalida(f'O produto "{slug}" precisa de um nome.')

        # Toda a validação de modelo antes da primeira gravação: formato, tamanho
        # e sigla contra o BANCO (`full_clean`).
        for produto in mexidos.values():
            _valida_texto(produto)
        # O `full_clean` não enxerga o próprio lote: no banco os produtos daqui
        # ainda têm a sigla velha, e duas siglas novas iguais passariam as duas
        # e estourariam no índice único do segundo `save()`.
        siglas = {}
        for produto in mexidos.values():
            outro = siglas.setdefault(produto.sigla, produto)
            if outro is not produto:
                raise PublicacaoInvalida(
                    f'A sigla {produto.sigla} ficaria em "{outro.nome}" e em "{produto.nome}". '
                    "Cada produto precisa de uma sigla só dele."
                )

        linhas, alvos = [], set()
        for chave, produto in mexidos.items():
            depois = fotografia(produto)
            mudancas = diferencas(antes[chave]["nome"], antes[chave], depois)
            if not mudancas:
                continue
            linhas.extend(mudancas)
            alvos.add(alvo_de_produto(produto))
            try:
                produto.save()
            except IntegrityError:
                # Rede de segurança para uma corrida: outro produto ganhou a mesma
                # sigla (um POST /api/produtos) entre a validação acima e esta
                # gravação. Sai do `atomic` com a exceção: nada do lote fica.
                raise PublicacaoInvalida(
                    f'"{produto.nome}" não pôde ser gravado: a sigla colide com outro '
                    "produto. Recarregue a página e confira as siglas."
                )
            registrar_produto(produto, autor=autor, quando=agora)

        for categoria, novo in referencias:
            if novo == categoria.valor_referencia:
                continue
            linhas.append(
                f"{categoria.nome}: valor de referência {descreve('valor_referencia', categoria.valor_referencia)}"
                f" → {descreve('valor_referencia', novo)}"
            )
            alvos.add(categoria.slug)
            categoria.valor_referencia = novo
            categoria.save(update_fields=["valor_referencia", "atualizado_em"])
            registrar_categoria(categoria, autor=autor, quando=agora)

        # Uma por clique, inclusive quando nada mudou: foi uma pessoa que apertou
        # "publicar", e é isso que a trilha de publicações registra.
        publicacao = grava_publicacao(autor=autor, linhas=linhas, alvos=alvos)

    return publicacao, linhas
