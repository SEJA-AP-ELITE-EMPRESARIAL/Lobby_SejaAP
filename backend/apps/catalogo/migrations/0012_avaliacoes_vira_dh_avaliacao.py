"""
"Avaliações" vira "DH - Avaliação", com sigla LBA e formulário próprio (14/10/2026).

O PRODUTO

Nasceu pelo /admin (aba Produtos), na categoria Produtos: slug `avaliacoes`,
sigla `AVL`, R$ 1.500 à vista e sem `fluxo` — ou seja, vendido pelo fluxo de
tabela, com o cronograma e o webhook do Elite. Por isso ele não está em nenhuma
migration anterior, e um banco montado só por elas (o de teste, o de quem
desenvolve) não o tem. Banco sem o produto: esta migration não faz nada.

O que muda nele:

- nome "DH - Avaliação" e sigla `LBA`. O slug fica: ele viaja no payload da
  venda (`produto_id` do comprovante) e é a chave da linha do tempo de valores;
- `fluxo="dh_avaliacao"`, que manda o consultor para o formulário próprio em vez
  do cronograma de parcelas;
- o valor à vista sai. Produto de fluxo próprio não guarda preço nenhum (a
  constraint `produto_de_fluxo_proprio_nao_tem_preco`, da `0009`): os valores
  dele são os das modalidades, configurados em `apps.dh_avaliacao`.

O QUE NÃO MUDA

Vendas antigas. O protocolo é texto gerado no navegador (`AVL-...`) e mora no
n8n, no Omie e no `ComprovanteVenda`, que esta migration não toca. Elas seguem
dizendo AVL, e é o certo: foi o que se vendeu.

A LINHA DO TEMPO

Como a `0010` e a `0011`, esta migration não grava `PublicacaoCatalogo`: não há
pessoa por trás dela, e publicação é o registro de quem apertou o botão. Mas,
diferente do produto da `0010`, este tem passado: uma `Vigencia` ABERTA dizendo
que ele custa R$ 1.500. Deixá-la aberta faria a aba Histórico afirmar um preço
que o produto não tem mais. Ela é FECHADA agora — não apagada: "valeu até esta
migration" é exatamente o que aconteceu.

AS CONFERÊNCIAS

Como a `0003` e a `0010`, ela para com a mensagem em português em vez de deixar o
banco reclamar: se a sigla LBA já estiver em uso, se houver mais de um produto
`avaliacoes` (um por categoria), se o produto já tiver outro fluxo ou se não for
avulso (o desfazer só sabe devolver um valor à vista).

O DESFAZER

Volta nome e sigla (`Avaliações`, `AVL`), tira o fluxo e devolve o valor à
vista: o último valor da linha do tempo, ou os R$ 1.500 do pedido se não houver
registro. A volta do valor entra como uma vigência NOVA, sem reabrir a antiga —
a trilha não se reescreve, nem para desfazer.

DEPLOY: esta migration e o `index.html` andam juntos, pelo motivo da `0010` — um
front que não conhece `dh_avaliacao` mostra o produto sem preço e não sabe
abri-lo. E o webhook do DH - Avaliação nasce vazio: até ele ser preenchido, o
produto abre e preenche, mas não envia. Hoje o Avaliações vende pelo webhook do
Elite, então publicar isto antes do webhook para as vendas dele.
"""
from decimal import Decimal

from django.db import migrations, models
from django.utils import timezone

SLUG = "avaliacoes"
FLUXO = "dh_avaliacao"

ANTES = {"nome": "Avaliações", "sigla": "AVL"}
DEPOIS = {"nome": "DH - Avaliação", "sigla": "LBA"}

# O valor à vista do pedido (14/10/2026). Só é usado no desfazer, e só se a
# linha do tempo não tiver o valor que o produto tinha.
VALOR_ANTES = Decimal("1500.00")

CAMPOS_DE_PRECO = ("mensalidade", "valor", "vigencia_meses")


def _sigla_em_uso(apps, sigla, produto):
    Produto = apps.get_model("catalogo", "Produto")
    Categoria = apps.get_model("catalogo", "Categoria")
    outro = Produto.objects.filter(sigla=sigla).exclude(pk=produto.pk).first()
    if outro is not None:
        return f'o produto "{outro.nome}"'
    categoria = Categoria.objects.filter(sigla=sigla).first()
    if categoria is not None:
        return f'a categoria "{categoria.nome}"'
    return None


def aplicar(apps, schema_editor):
    Produto = apps.get_model("catalogo", "Produto")
    Vigencia = apps.get_model("catalogo", "Vigencia")

    produtos = list(Produto.objects.filter(slug=SLUG))
    if not produtos:
        return
    if len(produtos) > 1:
        raise RuntimeError(
            f'Há {len(produtos)} produtos com o identificador "{SLUG}" neste banco, '
            "em categorias diferentes. Não dá para saber qual vira DH - Avaliação: "
            "resolva à mão no /django-admin/ e rode o migrate de novo."
        )
    produto = produtos[0]

    if produto.fluxo == FLUXO:
        # Já convertido (à mão, ou por uma rodada anterior desta migration).
        return
    if produto.fluxo:
        raise RuntimeError(
            f'"{produto.nome}" já tem o fluxo próprio "{produto.fluxo}". Esta '
            "migration só converte o Avaliações de tabela; confira o produto antes."
        )
    if produto.recorrente:
        raise RuntimeError(
            f'"{produto.nome}" está como recorrente, e o Avaliações era avulso (R$ 1.500 '
            "à vista). O desfazer desta migration não saberia devolver mensalidade e "
            "vigência: confira o produto antes de converter."
        )

    conflito = _sigla_em_uso(apps, DEPOIS["sigla"], produto)
    if conflito:
        raise RuntimeError(
            f"A sigla {DEPOIS['sigla']} já é de {conflito}. Ela é o SSS do protocolo "
            "da venda e não pode repetir: troque a sigla de lá e rode o migrate de novo."
        )

    agora = timezone.now()
    produto.nome = DEPOIS["nome"]
    produto.sigla = DEPOIS["sigla"]
    produto.fluxo = FLUXO
    produto.recorrente = False
    for campo in CAMPOS_DE_PRECO:
        setattr(produto, campo, None)
    produto.save()

    # Fecha, não apaga: ver o cabeçalho.
    Vigencia.objects.filter(
        chave=SLUG, campo__in=CAMPOS_DE_PRECO, vigente_ate__isnull=True
    ).update(vigente_ate=agora)


def desfazer(apps, schema_editor):
    Produto = apps.get_model("catalogo", "Produto")
    Vigencia = apps.get_model("catalogo", "Vigencia")

    produto = Produto.objects.filter(slug=SLUG, fluxo=FLUXO).first()
    if produto is None:
        return

    conflito = _sigla_em_uso(apps, ANTES["sigla"], produto)
    if conflito:
        raise RuntimeError(
            f"A sigla {ANTES['sigla']} passou a ser de {conflito}: devolver o "
            "Avaliações a ela repetiria o SSS do protocolo. Resolva à mão antes."
        )

    # O último valor de verdade da linha do tempo. Valor vazio é o que a tela
    # grava ao editar um produto de formulário (não há preço a registrar), e não
    # serve de preço.
    ultimo = (
        Vigencia.objects.filter(chave=SLUG, campo="valor")
        .exclude(valor="")
        .order_by("-vigente_de", "-id")
        .first()
    )
    try:
        valor = Decimal(ultimo.valor) if ultimo is not None else VALOR_ANTES
    except ArithmeticError:
        valor = VALOR_ANTES

    agora = timezone.now()
    produto.nome = ANTES["nome"]
    produto.sigla = ANTES["sigla"]
    produto.fluxo = ""
    produto.recorrente = False
    produto.mensalidade = None
    produto.vigencia_meses = None
    produto.valor = valor
    produto.save()

    Vigencia.objects.filter(chave=SLUG, campo="valor", vigente_ate__isnull=True).update(
        vigente_ate=agora
    )
    Vigencia.objects.create(
        chave=SLUG,
        rotulo=ANTES["nome"],
        campo="valor",
        valor=f"{valor:.2f}",
        vigente_de=agora,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0011_categoria_comeca_pelo_produto'),
    ]

    operations = [
        migrations.AlterField(
            model_name='produto',
            name='fluxo',
            field=models.CharField(blank=True, choices=[('dh', 'Formulário DH (Recrutamento e Seleção)'), ('dh_avaliacao', 'Formulário DH - Avaliação (PROFILER)')], default='', help_text='Vazio = produto de tabela: tem preço aqui e o consultor segue o fluxo padrão. Preenchido, o produto NÃO tem preço — o consultor cai num formulário próprio e os valores saem de lá. Produto assim é somente leitura para a tela da diretoria.', max_length=20, verbose_name='fluxo próprio'),
        ),
        migrations.RunPython(aplicar, desfazer),
    ]
