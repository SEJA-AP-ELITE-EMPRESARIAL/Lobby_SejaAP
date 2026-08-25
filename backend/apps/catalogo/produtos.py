"""
Criação e edição de produto pela tela da diretoria.

POR QUE ISTO SAIU DO /django-admin/

Criar produto sempre foi possível — no admin do Django, que é uma interface de
banco: rótulos meio em inglês, nenhum aviso sobre o que a mudança causa na tela
do consultor, e, o que mais pesa, **por fora do histórico**. Preço alterado por
lá não vira `PublicacaoCatalogo` nem `Vigencia`; simplesmente muda, e a trilha
mente por omissão.

Como o catálogo passou a crescer com frequência, o caminho de menor esforço não
podia continuar sendo o que não deixa rastro. O /django-admin/ segue existindo
para conserto e emergência — não para o dia a dia.

O QUE ESTE MÓDULO NÃO FAZ: APAGAR

Não há remoção de produto, e não é esquecimento. Produto vendido aparece no
protocolo de vendas já fechadas; apagá-lo deixa esses protocolos órfãos. É a
mesma razão do `on_delete=PROTECT` em `Produto.categoria`. Para tirar um produto
de circulação sem apagar história, a saída é a categoria travada — ou um campo
`ativo`, se um dia for preciso, que é mudança de modelo e não de tela.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

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
from .vigencias import registrar_produto


class ProdutoInvalido(Exception):
    """Recusa de gravação. A mensagem vai crua para a tela da diretoria."""


# Campos que a tela edita. `slug` fica de fora na EDIÇÃO de propósito: ele viaja
# no payload da venda enviado ao n8n, então mudá-lo depois de publicado quebra a
# conciliação de quem já comprou.
CAMPOS_TEXTO = ("nome", "descricao", "duracao", "icone")


def _decimal(bruto, campo, teto):
    try:
        valor = arredonda(Decimal(str(bruto).replace(",", ".").strip()))
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        raise ProdutoInvalido(f"{campo} inválido.")
    if not (Decimal(0) < valor <= teto):
        raise ProdutoInvalido(f"{campo} fora da faixa permitida.")
    return valor


def _aplica_valores(produto: Produto, dados: dict):
    """Preenche a metade recorrente OU a avulsa, e zera a outra.

    Zerar importa: as `CheckConstraint` do modelo exigem que só uma das duas
    exista. Um produto que era avulso e virou recorrente ficaria com `valor`
    preenchido, e o banco recusaria a gravação com um erro de constraint que não
    diz nada a quem está na tela.

    Produto de fluxo próprio não tem nenhuma das duas metades: os valores dele
    saem do formulário do consultor. A tela manda os campos de preço assim
    mesmo (o formulário dela é um só), então aqui eles são descartados em vez de
    recusados — o que a diretoria de fato edita nesse produto é nome, descrição
    e ícone.
    """
    if produto.de_formulario:
        produto.recorrente = False
        produto.mensalidade = None
        produto.vigencia_meses = None
        produto.valor = None
        return

    recorrente = bool(dados.get("recorrente"))
    produto.recorrente = recorrente
    if recorrente:
        produto.mensalidade = _decimal(dados.get("mensalidade"), "Mensalidade", MENSALIDADE_MAX)
        try:
            vigencia = int(dados.get("vigencia_meses"))
        except (TypeError, ValueError):
            raise ProdutoInvalido("Vigência inválida.")
        if not VIGENCIA_MIN <= vigencia <= VIGENCIA_MAX:
            raise ProdutoInvalido(
                f"Vigência fora da faixa: use de {VIGENCIA_MIN} a {VIGENCIA_MAX} meses."
            )
        produto.vigencia_meses = vigencia
        produto.valor = None
    else:
        produto.valor = _decimal(dados.get("valor"), "Valor à vista", VALOR_MAX)
        produto.mensalidade = None
        produto.vigencia_meses = None


def _aplica_comuns(produto: Produto, dados: dict):
    for campo in CAMPOS_TEXTO:
        if campo in dados:
            setattr(produto, campo, str(dados.get(campo) or "").strip())
    if "sigla" in dados:
        produto.sigla = normaliza_sigla(dados.get("sigla"))
    if "ordem" in dados:
        try:
            produto.ordem = max(0, int(dados.get("ordem") or 0))
        except (TypeError, ValueError):
            raise ProdutoInvalido("Ordem inválida.")


def _valida(produto: Produto):
    """Passa pelo `full_clean` do modelo e traduz o erro para a tela.

    O `full_clean` é o que roda a unicidade da sigla, o formato de 3 letras e a
    conferência contra as siglas de categoria. Sem ele, a colisão só apareceria
    como IntegrityError — 500 na cara da diretoria, em vez de "a sigla PRO já é
    do ELITE PRO".
    """
    try:
        produto.full_clean()
    except ValidationError as erro:
        campo, mensagens = next(iter(erro.message_dict.items()))
        try:
            rotulo = "" if campo == "__all__" else str(Produto._meta.get_field(campo).verbose_name)
        except FieldDoesNotExist:  # erro amarrado a algo que não é campo do modelo
            rotulo = ""
        prefixo = f"{rotulo.capitalize()}: " if rotulo else ""
        raise ProdutoInvalido(f"{prefixo}{mensagens[0]}")


@transaction.atomic
def cria(dados: dict, *, autor=None) -> Produto:
    """Cria um produto numa categoria existente."""
    if not isinstance(dados, dict):
        raise ProdutoInvalido("Corpo inválido.")

    categoria_slug = str(dados.get("categoria_id") or "").strip()
    try:
        categoria = Categoria.objects.get(slug=categoria_slug)
    except Categoria.DoesNotExist:
        raise ProdutoInvalido(f'Categoria "{categoria_slug}" não existe.')
    if categoria.gerenciada_em_codigo:
        raise ProdutoInvalido(
            f'"{categoria.nome}" tem fluxo próprio e não usa tabela de preços — '
            "não há produto a criar nela."
        )

    nome = str(dados.get("nome") or "").strip()
    if not nome:
        raise ProdutoInvalido("O produto precisa de um nome.")

    # O slug pode vir da tela ou sair do nome. Sair do nome é o caminho comum, e
    # evita que alguém invente um id com espaço ou acento — que iria no payload
    # da venda e no `find` do front.
    slug = slugify(str(dados.get("slug") or "").strip() or nome)[:40]
    if not slug:
        raise ProdutoInvalido("Não consegui derivar um identificador do nome.")
    if categoria.produtos.filter(slug=slug).exists():
        raise ProdutoInvalido(
            f'Já existe um produto "{slug}" em {categoria.nome}. Mude o nome ou '
            "informe outro identificador."
        )

    # A tela não cria produto de fluxo próprio, e é de propósito: cada fluxo é um
    # formulário escrito no `index.html`. Criar a linha sem o formulário do outro
    # lado põe no lobby um produto que não abre. Nasce no /django-admin/, junto
    # com o deploy do front — ver a migration `0010`.
    produto = Produto(categoria=categoria, slug=slug)
    _aplica_comuns(produto, dados)
    _aplica_valores(produto, dados)
    if "ordem" not in dados:
        ultimo = categoria.produtos.order_by("-ordem").first()
        produto.ordem = (ultimo.ordem + 1) if ultimo else 0

    _valida(produto)
    produto.save()
    registrar_produto(produto, autor=autor, quando=timezone.now())
    return produto


@transaction.atomic
def edita(categoria_slug: str, slug: str, dados: dict, *, autor=None) -> Produto:
    """Edita um produto existente. O `slug` e a categoria não mudam."""
    if not isinstance(dados, dict):
        raise ProdutoInvalido("Corpo inválido.")
    try:
        produto = Produto.objects.select_related("categoria").get(
            categoria__slug=categoria_slug, slug=slug
        )
    except Produto.DoesNotExist:
        raise ProdutoInvalido(
            f'Produto "{slug}" não existe em "{categoria_slug}". Recarregue a página.'
        )

    _aplica_comuns(produto, dados)
    if "recorrente" in dados:
        _aplica_valores(produto, dados)

    _valida(produto)
    produto.save()
    registrar_produto(produto, autor=autor, quando=timezone.now())
    return produto
