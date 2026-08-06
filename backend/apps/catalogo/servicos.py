"""
Publicação da tabela de preços — o porte do `normalizaCatalogo` do KV.

Origem: `functions/_lib/catalogo.js:102-165`. As mensagens de erro são as mesmas
palavra por palavra, porque o `admin.html:231` as exibe cruas para a diretoria.

UMA DIFERENÇA DELIBERADA em relação ao KV, e ela é grande:

    este serviço APLICA VALORES a linhas que já existem.
    Não cria categoria, não cria produto, não apaga nada.

No KV o PUT substituía o array inteiro: o que não viesse no corpo, sumia. Isso
funcionava porque o `/admin` sempre devolvia o catálogo completo que tinha
acabado de receber no GET. Mas significa que uma requisição malformada — ou um
navegador que perdeu metade do estado — apagava produtos em silêncio, e produto
apagado é protocolo de venda órfão.

Como o `/admin` só edita um número por produto (`admin.html:186-197`), nada se
perde ao recusar o resto. Mudança estrutural (criar produto, renomear categoria,
reordenar) passa pelo /django-admin/, onde é uma ação consciente e auditada, não
efeito colateral de um PUT.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (
    MENSALIDADE_MAX,
    VALOR_MAX,
    VIGENCIA_MAX,
    VIGENCIA_MIN,
    Categoria,
    Produto,
    PublicacaoCatalogo,
    arredonda,
)
from .serializers import serializa_catalogo


class CatalogoInvalido(Exception):
    """Recusa de publicação. A mensagem vai crua para a tela da diretoria."""


@dataclass
class Alteracao:
    """Uma mudança de valor, para o resumo do histórico."""

    produto: str
    de: Decimal
    para: Decimal

    def __str__(self) -> str:
        return f"{self.produto}: {self.de} → {self.para}"


def _numero(valor):
    """Equivalente ao `num` do KV (`catalogo.js:87`): aceita vírgula decimal.

    O KV precisava disso porque o admin mandava o que o `MoneyInput` produzisse.
    Mantido para não recusar uma publicação que hoje passa.
    """
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    try:
        return Decimal(str(valor if valor is not None else "").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise CatalogoInvalido("Valor numérico inválido no catálogo.")


def _valor_publicado(bruto: dict, produto: Produto) -> Decimal:
    """Extrai e valida o valor de um produto, na ordem exata do validador do KV.

    Ordem importa: vigência antes de mensalidade, mensalidade antes do total.
    Quem inverte troca a mensagem de erro que a diretoria vê.
    """
    nome = produto.nome

    if produto.recorrente:
        # `vigencia` cai em 12 quando ausente ou zero — é o `|| 12` de catalogo.js:127.
        try:
            vigencia = int(_numero(bruto.get("vigencia")) or 12)
        except (InvalidOperation, ValueError, TypeError):
            vigencia = 0
        if not VIGENCIA_MIN <= vigencia <= VIGENCIA_MAX:
            raise CatalogoInvalido(
                f'Vigência inválida em "{nome}": use de {VIGENCIA_MIN} a {VIGENCIA_MAX} meses.'
            )
        if vigencia != produto.vigencia_meses:
            # O /admin não edita vigência (`admin.html:348` só a exibe). Divergência
            # aqui significa payload adulterado ou tela desatualizada — recusar é
            # mais seguro do que aceitar e recalcular o total por cima.
            raise CatalogoInvalido(
                f'Vigência de "{nome}" não confere com a do catálogo. Recarregue a página.'
            )

        mensalidade = arredonda(_numero(bruto.get("monthly")))
        if not (Decimal(0) < mensalidade <= MENSALIDADE_MAX):
            raise CatalogoInvalido(f'Mensalidade inválida em "{nome}".')

        total = arredonda(mensalidade * vigencia)
        if not (Decimal(0) < total <= VALOR_MAX):
            raise CatalogoInvalido(f'Valor inválido em "{nome}".')
        return mensalidade

    total = arredonda(_numero(bruto.get("price")))
    if not (Decimal(0) < total <= VALOR_MAX):
        raise CatalogoInvalido(f'Valor inválido em "{nome}".')
    return total


def publica_catalogo(cats_recebidas, *, autor=None) -> tuple[list[dict], list[Alteracao]]:
    """Aplica os valores recebidos e grava uma publicação no histórico.

    Devolve `(catalogo_serializado, alteracoes)`. Levanta `CatalogoInvalido` sem
    ter tocado no banco — a transação garante que não existe publicação pela
    metade, que é a mesma promessa do comentário em `catalogo.js:92`.
    """
    if not isinstance(cats_recebidas, list):
        raise CatalogoInvalido("O catálogo precisa ser uma lista com ao menos uma categoria.")

    # Categorias de fluxo próprio são descartadas na escrita: o /admin devolve a
    # APN no PUT porque a recebeu no GET, mas ela não tem tabela para editar.
    # O KV filtrava por id (`catalogo.js:76`); aqui o critério é o fluxo, que é o
    # que o front de fato lê.
    slugs_em_codigo = set(
        Categoria.objects.exclude(fluxo="").values_list("slug", flat=True)
    )
    editaveis = [
        c for c in cats_recebidas
        if isinstance(c, dict) and c.get("id") not in slugs_em_codigo
    ]
    if not editaveis:
        raise CatalogoInvalido("O catálogo precisa ser uma lista com ao menos uma categoria.")

    alteracoes: list[Alteracao] = []

    with transaction.atomic():
        produtos_por_chave = {
            (p.categoria.slug, p.slug): p
            for p in Produto.objects.select_related("categoria").select_for_update()
        }
        vistos = set()

        for bruta in editaveis:
            slug_cat = bruta.get("id")
            nome_cat = bruta.get("name")
            if not slug_cat or not nome_cat:
                raise CatalogoInvalido(f"Categoria sem id ou nome: {slug_cat!r}")
            if slug_cat in vistos:
                raise CatalogoInvalido(f'Categoria duplicada: "{slug_cat}".')
            vistos.add(slug_cat)

            produtos_brutos = bruta.get("products")
            if not isinstance(produtos_brutos, list) or not produtos_brutos:
                raise CatalogoInvalido(f'Categoria "{nome_cat}" está sem produtos.')

            for bruto in produtos_brutos:
                if not isinstance(bruto, dict) or not bruto.get("id") or not bruto.get("name"):
                    raise CatalogoInvalido(f'Produto sem id ou nome na categoria "{nome_cat}".')

                produto = produtos_por_chave.get((slug_cat, bruto["id"]))
                if produto is None:
                    # Ver o cabeçalho: publicar não cria linha. Um id desconhecido
                    # é quase sempre tela velha depois de mudança estrutural.
                    raise CatalogoInvalido(
                        f'Produto "{bruto["id"]}" não existe na categoria "{nome_cat}". '
                        "Recarregue a página."
                    )

                novo = _valor_publicado(bruto, produto)
                atual = produto.mensalidade if produto.recorrente else produto.valor

                if novo != atual:
                    alteracoes.append(Alteracao(produto=produto.nome, de=atual, para=novo))
                    if produto.recorrente:
                        produto.mensalidade = novo
                    else:
                        produto.valor = novo
                    produto.save(update_fields=["mensalidade", "valor", "atualizado_em"])

        catalogo = serializa_catalogo(
            Categoria.objects.prefetch_related("produtos").all()
        )

        PublicacaoCatalogo.objects.create(
            autor=autor if autor and autor.is_authenticated else None,
            autor_email=getattr(autor, "email", "") or "",
            catalogo=catalogo,
            resumo="\n".join(str(a) for a in alteracoes),
        )

    return catalogo, alteracoes
