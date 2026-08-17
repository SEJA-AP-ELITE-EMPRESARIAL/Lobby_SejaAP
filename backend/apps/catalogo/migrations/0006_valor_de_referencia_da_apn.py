"""
A APN deixa de ser somente leitura na tela da diretoria.

O QUE ESTAVA TRAVADO, E O QUE NÃO ESTAVA

No lobby, o valor da APN **sempre** foi livre: o consultor digita e pronto, sem
autorização, porque APN é venda de valor negociado (decisão de 31/07/2026). O que
era somente leitura é o `/admin`: a categoria aparecia lá com a frase "sem valor
de tabela" e nada para editar, porque `publica_catalogo` descartava categoria de
fluxo próprio inteira na escrita.

`valor_referencia` é o meio-termo que destrava isso sem desfazer a decisão de
produto: a diretoria configura o valor com que a tela do consultor ABRE, e ele
continua livre para alterar. Vazio = abre em branco, como sempre foi.

POR QUE UMA `CheckConstraint` E NÃO SÓ O `help_text`

Valor de referência numa categoria comum (Elite, Palestras) seria um número que
nenhum código lê — e o primeiro a lê-lo por engano estaria cotando errado. Como
o campo é preenchido no /django-admin/ e por script, a garantia tem que estar no
banco.

A escolha nova de `Vigencia.Campo` entra junto: o valor de referência aparece na
linha do tempo como qualquer outro valor, com autor e período.
"""
import django.core.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0005_elite_completa_e_limpeza'),
    ]

    operations = [
        migrations.AddField(
            model_name='categoria',
            name='valor_referencia',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Só em categoria de fluxo próprio (a APN). É o valor com que a tela do consultor ABRE — ele continua livre para alterar, porque a APN é venda de valor negociado. Vazio = a tela abre em branco, como antes.', max_digits=12, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0.01')), django.core.validators.MaxValueValidator(Decimal('100000000'))], verbose_name='valor de referência'),
        ),
        migrations.AlterField(
            model_name='vigencia',
            name='campo',
            field=models.CharField(choices=[('mensalidade', 'Mensalidade'), ('valor', 'Valor à vista'), ('valor_referencia', 'Valor de referência'), ('vigencia_meses', 'Vigência (meses)'), ('dia_vencimento', 'Dia do vencimento'), ('primeiro_vencimento', 'Primeira parcela'), ('entrada_prazo_dias', 'Prazo da entrada (dias)')], max_length=30, verbose_name='campo'),
        ),
        migrations.AddConstraint(
            model_name='categoria',
            constraint=models.CheckConstraint(condition=models.Q(('valor_referencia__isnull', True), models.Q(('fluxo', ''), _negated=True), _connector='OR'), name='valor_referencia_so_em_fluxo_proprio'),
        ),
    ]
