"""
"Produtos" passa a começar pela escolha do produto (14/10/2026).

Até aqui a ordem das etapas vinha só do fluxo: categoria sem `flow` e produto
sem `flow` caíam no padrão Empresa → Produto → Pagamento → Resumo. Em "Produtos"
isso pedia a empresa antes de saber qual produto seria vendido — e vão entrar
produtos cujos dados de contratante são outros. A ordem vira propriedade da
categoria, e não um `if` pelo id do produto, para que produto novo de tabela
nesta categoria também comece pela lista.

A categoria é achada pelo slug `produtos` (o da `0010`). Banco sem ela não tem
nada a marcar; a flag continua editável no /django-admin/.
"""
from django.db import migrations, models

SLUG = "produtos"


def aplicar(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Categoria.objects.filter(slug=SLUG).update(produto_primeiro=True)


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0010_categoria_produtos_e_recrutamento'),
    ]

    operations = [
        migrations.AddField(
            model_name='categoria',
            name='produto_primeiro',
            field=models.BooleanField(default=False, help_text='A venda abre na lista de produtos, e só depois pede os dados de quem contrata. Para categoria cujos produtos têm formulários de contratante diferentes (hoje, Produtos): é o produto que decide qual formulário vem. Só muda a ordem do fluxo de tabela: a APN e o formulário DH têm etapas próprias. Vai ao JSON como `produtoPrimeiro`.', verbose_name='começa pela escolha do produto'),
        ),
        # Na volta, a coluna some com o RemoveField: não há o que desfazer aqui.
        migrations.RunPython(aplicar, migrations.RunPython.noop),
    ]
