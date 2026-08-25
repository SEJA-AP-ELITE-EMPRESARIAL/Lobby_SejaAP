"""
"Palestras" vira "Produtos", destravada, com o Recrutamento e Seleção dentro.

O QUE ACONTECE COM O SLUG

A categoria muda de `palestras` para `produtos`. Slug publicado normalmente NÃO
se mexe — ele viaja no payload da venda como `categoria_id` e é por ele que a
conciliação sabe o que foi vendido. Aqui a mudança é segura por um motivo
específico, não por descuido: `palestras` nunca vendeu nada. Nasceu com três
produtos na semente (`0002`), perdeu os três sem nunca ter aberto (`0005`) e
ficou desde então TRAVADA — categoria travada não abre no lobby, então não
existe protocolo que aponte para ela.

Mesmo assim a conferência contra `ComprovanteVenda` está abaixo, na mesma linha
do que a `0007` faz: se este banco tiver uma venda com `categoria_id:
"palestras"`, a migração PARA em vez de renomear e deixar o protocolo órfão. A
conferência não é prova — o comprovante só existe desde 07/08/2026, e antes
disso o navegador postava direto no n8n. É a garantia que dá para ter aqui.

O PRODUTO

`Recrutamento e Seleção`, sigla `RES`, `fluxo="dh"`: sem preço, porque os
valores dele (salário de referência e adiantamento, vaga a vaga) só existem
depois que o cliente preenche o Formulário DH. É o primeiro produto de fluxo
próprio do catálogo — ver a `0009`.

Não há `Vigencia` a registrar: vigência é histórico de VALOR, e este produto não
tem valor a historiar. O dia em que ganhar um, a linha do tempo dele começa ali.

DEPLOY: esta migração e o `index.html` andam juntos. O produto só sabe abrir o
formulário num front que conheça o fluxo `dh`; num front antigo ele aparece na
lista sem preço e não leva a lugar nenhum. Aplicar a migração sem publicar o
front novo é deixar um produto quebrado na tela do consultor.
"""
from django.db import migrations

SLUG_ANTIGO = "palestras"
SLUG_NOVO = "produtos"

PRODUTO = {
    "slug": "recrutamento-selecao",
    "nome": "Recrutamento e Seleção",
    "sigla": "RES",
    "descricao": "Processo seletivo conduzido pela Seja AP, vaga a vaga",
    "duracao": "por vaga",
    "icone": "person_search",
    "fluxo": "dh",
    "ordem": 0,
}


def aplicar(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Produto = apps.get_model("catalogo", "Produto")
    ComprovanteVenda = apps.get_model("vendas", "ComprovanteVenda")

    categoria = Categoria.objects.filter(slug=SLUG_ANTIGO).first()
    if categoria is None:
        # Banco onde a categoria já foi renomeada à mão, ou nunca existiu.
        categoria = Categoria.objects.filter(slug=SLUG_NOVO).first()
        if categoria is None:
            return
    else:
        vendido = ComprovanteVenda.objects.filter(
            valores__categoria_id=SLUG_ANTIGO
        ).exists()
        if vendido:
            raise RuntimeError(
                'Existe venda emitida com categoria_id "palestras" neste banco: '
                "renomear o slug deixaria o protocolo dela apontando para uma "
                "categoria que não existe mais. Trate a renomeação à mão, "
                "mantendo o slug antigo, e tire esta parte da migração."
            )
        categoria.slug = SLUG_NOVO

    categoria.nome = "Produtos"
    categoria.descricao = (
        "Produtos e serviços avulsos — cada um com o seu formulário de contratação."
    )
    categoria.icone = "inventory_2"
    # Destravada: sem isso o card continua aparecendo como "em implementação" e
    # o consultor não consegue abrir o produto que esta migração acabou de criar.
    categoria.travada = False
    categoria.save()

    Produto.objects.update_or_create(
        categoria=categoria,
        slug=PRODUTO["slug"],
        defaults={k: v for k, v in PRODUTO.items() if k != "slug"},
    )


def desfazer(apps, schema_editor):
    """Volta a categoria a "Palestras" travada e remove o produto.

    Removível sem cerimônia justamente porque, se houver venda dele, a `0009`
    não desce (a constraint some) e nada disto chega a rodar — mas a conferência
    fica, pelo mesmo motivo da ida: comprovante emitido é protocolo lá fora.
    """
    Categoria = apps.get_model("catalogo", "Categoria")
    Produto = apps.get_model("catalogo", "Produto")
    ComprovanteVenda = apps.get_model("vendas", "ComprovanteVenda")

    categoria = Categoria.objects.filter(slug=SLUG_NOVO).first()
    if categoria is None:
        return

    vendido = ComprovanteVenda.objects.filter(
        valores__categoria_id=SLUG_NOVO, valores__produto_id=PRODUTO["slug"]
    ).exists()
    if vendido:
        raise RuntimeError(
            "O Recrutamento e Seleção tem venda emitida neste banco: apagá-lo "
            "deixaria o protocolo dela sem produto."
        )

    Produto.objects.filter(categoria=categoria, slug=PRODUTO["slug"]).delete()
    categoria.slug = SLUG_ANTIGO
    categoria.nome = "Palestras"
    categoria.descricao = "Palestras in company e eventos de alto impacto."
    categoria.icone = "campaign"
    categoria.travada = True
    categoria.save()


class Migration(migrations.Migration):
    dependencies = [
        ("catalogo", "0009_fluxo_proprio_de_produto"),
        ("vendas", "0001_initial"),
    ]
    operations = [migrations.RunPython(aplicar, desfazer)]
