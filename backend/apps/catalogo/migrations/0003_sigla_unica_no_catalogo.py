"""
`sigla` passa a ser única no catálogo.

POR QUE ESTA MIGRAÇÃO EXISTE

A sigla é o `SSS` do protocolo da venda (`SSS-YYMMDDPRRRRR`) — o único campo do
protocolo que diz O QUE foi vendido. Até aqui nada impedia duas linhas com a
mesma sigla: não daria erro em lugar nenhum, só produziria protocolos
indistinguíveis, e o problema apareceria na conciliação, meses depois.

Isso passou a importar agora porque o catálogo vai crescer (a Elite recebe
produtos novos), e produto é criado à mão no /django-admin/, onde a única defesa
contra repetir uma sigla era a memória de quem digita.

POR QUE A CONFERÊNCIA VEM ANTES DO `AlterField`

Nesta casa as migrations são aplicadas à mão, em produção
(`docs/06_DEPLOY.md`). Se houver sigla repetida no banco, o `AlterField` falha
com um erro de índice do Postgres que não diz qual linha é o problema. A
conferência abaixo falha antes, dizendo a sigla e os produtos envolvidos —
resolve-se em um `UPDATE`, não em uma investigação.

O passo de normalização (`upper()`) vem junto porque o modelo agora normaliza no
`save()`: sem ele, uma sigla gravada em minúscula por script antigo continuaria
divergindo da mesma sigla em maiúscula, e as duas conviveriam sob o índice único.
"""
from collections import defaultdict

import django.core.validators
from django.db import migrations, models


def normaliza_e_confere(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Produto = apps.get_model("catalogo", "Produto")

    for modelo in (Categoria, Produto):
        for linha in modelo.objects.exclude(sigla=""):
            normalizada = str(linha.sigla or "").strip().upper()
            if normalizada != linha.sigla:
                linha.sigla = normalizada
                linha.save(update_fields=["sigla"])

    donos = defaultdict(list)
    for produto in Produto.objects.select_related("categoria"):
        donos[produto.sigla].append(f"produto {produto.categoria.slug}/{produto.slug}")
    for categoria in Categoria.objects.exclude(sigla=""):
        donos[categoria.sigla].append(f"categoria {categoria.slug}")

    repetidas = {sigla: quem for sigla, quem in donos.items() if len(quem) > 1}
    if repetidas:
        detalhe = "; ".join(f"{sigla} → {', '.join(quem)}" for sigla, quem in sorted(repetidas.items()))
        raise RuntimeError(
            "Há sigla repetida no catálogo, e ela é o que identifica o produto no "
            f"protocolo da venda. Corrija antes de aplicar esta migração: {detalhe}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0002_semear_catalogo'),
    ]

    operations = [
        # `migrations.RunPython.noop` na volta: desfazer a unicidade é o
        # `AlterField` reverso: não há o que reverter em ter posto tudo em
        # maiúscula.
        migrations.RunPython(normaliza_e_confere, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='categoria',
            name='sigla',
            field=models.CharField(blank=True, default='', help_text='Só usada em categoria de fluxo próprio, onde não há produto para tirar a sigla do protocolo da venda. Não pode repetir a sigla de nenhum produto nem de outra categoria.', max_length=3, validators=[django.core.validators.RegexValidator('^[A-Z]{3}$', 'Use exatamente 3 letras maiúsculas.')], verbose_name='sigla'),
        ),
        migrations.AlterField(
            model_name='produto',
            name='sigla',
            field=models.CharField(help_text='Alimenta o protocolo da venda (SSS-YYMMDDPRRRRR). Única no catálogo inteiro: é por ela que se sabe, meses depois, o que foi vendido.', max_length=3, unique=True, validators=[django.core.validators.RegexValidator('^[A-Z]{3}$', 'Use exatamente 3 letras maiúsculas.')], verbose_name='sigla'),
        ),
    ]
