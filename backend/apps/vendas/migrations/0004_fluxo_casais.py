"""
O comprovante passa a conhecer o Casais de Alta Performance (TSK-1622).

Só as `choices` mudam: `casais` tem 6 letras e a coluna já tem 20 (a `0003`).
Nenhum `ALTER` no banco, nos dois sentidos — o desfazer não precisa apagar nada.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vendas', '0003_fluxo_dh_avaliacao'),
    ]

    operations = [
        migrations.AlterField(
            model_name='comprovantevenda',
            name='fluxo',
            field=models.CharField(choices=[('elite', 'Elite (produto de tabela)'), ('apn', 'APN (valor livre)'), ('dh', 'DH (valores do formulário)'), ('dh_avaliacao', 'DH - Avaliação (itens do formulário)'), ('casais', 'Casais de Alta Performance (valor fixo)')], max_length=20, verbose_name='fluxo'),
        ),
    ]
