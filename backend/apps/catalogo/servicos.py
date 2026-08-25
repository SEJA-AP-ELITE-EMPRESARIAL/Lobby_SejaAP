"""
Publicação da tabela de preços — o porte do `normalizaCatalogo` do KV.

Origem: `functions/_lib/catalogo.js:102-165` — arquivo removido da árvore, hoje
só no histórico (`git show 338e932`); ver a nota em `models.py`. As mensagens de
erro são as mesmas palavra por palavra, porque o `admin.html:231` as exibe cruas
para a diretoria.

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
from django.utils import timezone

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
from .vigencias import registrar_categoria, registrar_produto


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


def _publica_valor_referencia(bruta: dict, alteracoes: list, *, autor, quando):
    """Aplica o valor de referência de uma categoria de fluxo próprio (a APN).

    O campo é OPCIONAL nos dois sentidos: ausente no corpo significa "não mexi
    nisto" e é ignorado; presente e vazio significa "quero limpar", e a tela do
    consultor volta a abrir em branco. Sem essa distinção não haveria como
    desfazer um valor de referência depois de configurado.
    """
    if "valor_referencia" not in bruta:
        return

    slug = bruta.get("id")
    try:
        categoria = Categoria.objects.select_for_update().get(slug=slug)
    except Categoria.DoesNotExist:
        raise CatalogoInvalido(f'Categoria "{slug}" não existe. Recarregue a página.')

    bruto = bruta.get("valor_referencia")
    if bruto in (None, ""):
        novo = None
    else:
        novo = arredonda(_numero(bruto))
        if not (Decimal(0) < novo <= VALOR_MAX):
            raise CatalogoInvalido(f'Valor de referência inválido em "{categoria.nome}".')

    if novo == categoria.valor_referencia:
        return

    alteracoes.append(
        Alteracao(
            produto=f"{categoria.nome} (valor de referência)",
            de=categoria.valor_referencia,
            para=novo,
        )
    )
    categoria.valor_referencia = novo
    categoria.save(update_fields=["valor_referencia", "atualizado_em"])
    registrar_categoria(categoria, autor=autor, quando=quando)


def publica_catalogo(cats_recebidas, *, autor=None) -> tuple[list[dict], list[Alteracao]]:
    """Aplica os valores recebidos e grava uma publicação no histórico.

    Devolve `(catalogo_serializado, alteracoes)`. Levanta `CatalogoInvalido` sem
    ter tocado no banco — a transação garante que não existe publicação pela
    metade, que é a mesma promessa do comentário em `catalogo.js:92`.
    """
    if not isinstance(cats_recebidas, list):
        raise CatalogoInvalido("O catálogo precisa ser uma lista com ao menos uma categoria.")

    # Categoria de fluxo próprio (a APN) não tem tabela de PRODUTOS para editar —
    # ela fica fora do laço de produtos, abaixo. O KV a descartava por inteiro
    # (`catalogo.js:76`); desde 17/08/2026 ela tem um campo editável, o VALOR DE
    # REFERÊNCIA com que a tela do consultor abre, então é separada em vez de
    # jogada fora. O critério continua sendo o `fluxo`, não o slug, porque é
    # `flow` que o front de fato lê.
    slugs_em_codigo = set(
        Categoria.objects.exclude(fluxo="").values_list("slug", flat=True)
    )
    editaveis, de_fluxo_proprio = [], []
    for c in cats_recebidas:
        if not isinstance(c, dict):
            continue
        (de_fluxo_proprio if c.get("id") in slugs_em_codigo else editaveis).append(c)

    # O corpo precisa trazer o catálogo de verdade. Só a APN significa tela
    # desatualizada ou requisição truncada — e publicar em cima disso, mesmo sem
    # apagar nada, é aplicar uma decisão que ninguém tomou.
    if not editaveis:
        raise CatalogoInvalido("O catálogo precisa ser uma lista com ao menos uma categoria.")

    alteracoes: list[Alteracao] = []
    agora = timezone.now()

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
            if not isinstance(produtos_brutos, list):
                # Sem a chave, ou com outra coisa no lugar da lista, é corpo
                # malformado — e aceitar isso seria publicar sobre uma tela que
                # perdeu metade do estado.
                raise CatalogoInvalido(f'Categoria "{nome_cat}" está sem a lista de produtos.')
            if not produtos_brutos:
                # Lista VAZIA é legítima desde 17/08/2026: Treinamentos existe
                # travada e sem produto nenhum, esperando os de verdade.
                # Antes disso toda categoria tinha produto, e o vazio só podia ser
                # erro — por isso este caso era recusado junto com o malformado.
                continue

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
                if produto.fluxo:
                    # Produto de fluxo próprio (o Recrutamento e Seleção) não tem
                    # valor de tabela: os dele saem do formulário, venda a venda.
                    # Ignorar em vez de recusar é o que mantém a publicação
                    # inteira funcionando — a tela manda o catálogo completo de
                    # volta, e recusar por causa de um produto que ela nem edita
                    # travaria a diretoria de publicar qualquer preço.
                    continue

                novo = _valor_publicado(bruto, produto)
                atual = produto.mensalidade if produto.recorrente else produto.valor

                if novo != atual:
                    alteracoes.append(Alteracao(produto=produto.nome, de=atual, para=novo))
                    if produto.recorrente:
                        produto.mensalidade = novo
                    else:
                        produto.valor = novo
                    produto.save(update_fields=["mensalidade", "valor", "atualizado_em"])
                    # Fecha a vigência do valor antigo e abre a do novo. Todos os
                    # produtos da mesma publicação compartilham `agora`: uma
                    # publicação é um instante, não uma sequência de instantes —
                    # senão a linha do tempo mostraria a tabela mudando "aos
                    # poucos" numa ordem que é só a do laço.
                    registrar_produto(produto, autor=autor, quando=agora)

        for bruta in de_fluxo_proprio:
            _publica_valor_referencia(bruta, alteracoes, autor=autor, quando=agora)

        catalogo = serializa_catalogo(
            Categoria.objects.prefetch_related("produtos__politica_cobranca").all()
        )

        PublicacaoCatalogo.objects.create(
            autor=autor if autor and autor.is_authenticated else None,
            autor_email=getattr(autor, "email", "") or "",
            catalogo=catalogo,
            resumo="\n".join(str(a) for a in alteracoes),
        )

    return catalogo, alteracoes
