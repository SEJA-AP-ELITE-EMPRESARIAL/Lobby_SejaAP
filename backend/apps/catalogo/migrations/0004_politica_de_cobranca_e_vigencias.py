"""
As datas de cobrança saem do código e viram dado, e nasce a linha do tempo.

DUAS TABELAS, DOIS PROBLEMAS DIFERENTES

`PoliticaCobranca` é o que o `index.html` tinha em constantes até 17/08/2026:
dia do vencimento (15), em que mês cai a primeira parcela e o prazo da entrada.
Enquanto isso foi código, mudar a data de cobrança era deploy do front — e essa
data acompanha calendário de cobrança, ou seja, muda com frequência e com pressa.

`Vigencia` responde uma pergunta que o `PublicacaoCatalogo` não responde: "quanto
custava em março?". A publicação registra o evento ("fulano mudou de X para Y");
esta registra o estado ("Y valeu de tal data até tal data"). Ver o docstring do
modelo.

A SEMENTE, E POR QUE ELA USA `criado_em`

Sem semente, o histórico nasceria vazio e o preço em vigor hoje apareceria na
tela sem origem — como se ninguém o tivesse definido. A semente abre uma vigência
para cada valor atual usando a **data de criação da linha**, não a data da
migração: é a melhor aproximação honesta de "desde quando isto vale". Carimbar
tudo com hoje diria que a tabela de preços inteira nasceu no dia do deploy, o que
não aconteceu.

Autor fica nulo nessas linhas, como já acontece na semente do catálogo — não
houve pessoa, houve migração.
"""
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# Os padrões que estavam no `index.html` na véspera desta migração. Repetidos
# aqui como literal, e não importados de `models.py`, pelo motivo de sempre:
# migração é história, e história não pode mudar quando o padrão do modelo mudar.
DIA_VENCIMENTO_INICIAL = 15
PRIMEIRO_VENCIMENTO_INICIAL = "mes_seguinte"
ENTRADA_PRAZO_INICIAL = 0


def _texto_dinheiro(valor):
    """Mesma forma canônica de `vigencias.texto` — duas casas, sempre."""
    return f"{valor:.2f}"


def semear(apps, schema_editor):
    Produto = apps.get_model("catalogo", "Produto")
    PoliticaCobranca = apps.get_model("catalogo", "PoliticaCobranca")
    Vigencia = apps.get_model("catalogo", "Vigencia")

    politica, _ = PoliticaCobranca.objects.get_or_create(
        geral=True,
        defaults={
            "produto": None,
            "dia_vencimento": DIA_VENCIMENTO_INICIAL,
            "primeiro_vencimento": PRIMEIRO_VENCIMENTO_INICIAL,
            "entrada_prazo_dias": ENTRADA_PRAZO_INICIAL,
        },
    )

    linhas = []
    for produto in Produto.objects.all():
        if produto.recorrente:
            campos = [
                ("mensalidade", _texto_dinheiro(produto.mensalidade)),
                ("vigencia_meses", str(produto.vigencia_meses)),
            ]
        else:
            campos = [("valor", _texto_dinheiro(produto.valor))]
        linhas += [
            Vigencia(
                chave=produto.slug,
                rotulo=produto.nome,
                campo=campo,
                valor=valor,
                vigente_de=produto.criado_em,
            )
            for campo, valor in campos
        ]

    linhas += [
        Vigencia(
            chave="geral",
            rotulo="Política de cobrança",
            campo=campo,
            valor=str(valor),
            vigente_de=politica.criado_em,
        )
        for campo, valor in (
            ("dia_vencimento", politica.dia_vencimento),
            ("primeiro_vencimento", politica.primeiro_vencimento),
            ("entrada_prazo_dias", politica.entrada_prazo_dias),
        )
    ]

    Vigencia.objects.bulk_create(linhas)


def limpar(apps, schema_editor):
    """A volta apaga só o que a semente plantou — as tabelas somem em seguida."""
    apps.get_model("catalogo", "Vigencia").objects.all().delete()
    apps.get_model("catalogo", "PoliticaCobranca").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0003_sigla_unica_no_catalogo'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PoliticaCobranca',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('geral', models.BooleanField(default=False, help_text='A política que vale para todo produto sem exceção própria. Existe uma, e só uma.', verbose_name='política geral')),
                ('dia_vencimento', models.PositiveSmallIntegerField(default=15, help_text='Dia do mês em que TODA parcela vence (1 a 28). Para em 28 porque 29, 30 e 31 não existem em todo mês, e o ponto de fixar o dia é ter uma data única de cobrança.', validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(28)], verbose_name='dia do vencimento')),
                ('primeiro_vencimento', models.CharField(choices=[('mes_seguinte', 'Dia do vencimento do mês SEGUINTE ao da venda'), ('proximo', 'Primeiro dia do vencimento DEPOIS da venda')], default='mes_seguinte', max_length=20, verbose_name='primeira parcela')),
                ('entrada_prazo_dias', models.PositiveSmallIntegerField(default=0, help_text='0 = a entrada é paga no dia da venda (o padrão). Acima disso, a data da entrada abre com este tanto de dias à frente.', validators=[django.core.validators.MaxValueValidator(90)], verbose_name='prazo da entrada (dias)')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('produto', models.OneToOneField(blank=True, help_text='Vazio na política geral. Preenchido, é a exceção deste produto.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='politica_cobranca', to='catalogo.produto', verbose_name='produto')),
            ],
            options={
                'verbose_name': 'política de cobrança',
                'verbose_name_plural': 'políticas de cobrança',
                'ordering': ('-geral', 'produto__categoria', 'produto__ordem', 'id'),
                'constraints': [models.UniqueConstraint(condition=models.Q(('geral', True)), fields=('geral',), name='uma_unica_politica_geral'), models.CheckConstraint(condition=models.Q(models.Q(('geral', True), ('produto__isnull', True)), models.Q(('geral', False), ('produto__isnull', False)), _connector='OR'), name='politica_geral_ou_de_produto')],
            },
        ),
        migrations.CreateModel(
            name='Vigencia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chave', models.CharField(db_index=True, help_text="O slug do produto, ou 'geral' para a política de cobrança.", max_length=40, verbose_name='chave')),
                ('rotulo', models.CharField(help_text='Como o alvo se chamava quando o registro foi criado. Fotografia, não referência: renomear o produto não reescreve o passado.', max_length=120, verbose_name='rótulo')),
                ('campo', models.CharField(choices=[('mensalidade', 'Mensalidade'), ('valor', 'Valor à vista'), ('vigencia_meses', 'Vigência (meses)'), ('dia_vencimento', 'Dia do vencimento'), ('primeiro_vencimento', 'Primeira parcela'), ('entrada_prazo_dias', 'Prazo da entrada (dias)')], max_length=30, verbose_name='campo')),
                ('valor', models.CharField(help_text='Texto, porque a mesma tabela guarda dinheiro, dia do mês e escolha de regra. Quem lê sabe o tipo pelo `campo`.', max_length=40, verbose_name='valor')),
                ('vigente_de', models.DateTimeField(db_index=True, verbose_name='vigente de')),
                ('vigente_ate', models.DateTimeField(blank=True, help_text='Nulo = é o valor que está valendo agora.', null=True, verbose_name='vigente até')),
                ('autor_email', models.EmailField(blank=True, default='', help_text='Cópia no momento do registro — a conta pode ser desativada depois, e o histórico não pode perder quem assinou.', max_length=254, verbose_name='e-mail do autor')),
                ('autor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='vigencias', to=settings.AUTH_USER_MODEL, verbose_name='autor')),
            ],
            options={
                'verbose_name': 'vigência de valor',
                'verbose_name_plural': 'vigências de valor',
                'ordering': ('-vigente_de', 'chave', 'campo'),
                'indexes': [models.Index(fields=['chave', 'campo', '-vigente_de'], name='catalogo_vi_chave_c00497_idx')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('vigente_ate__isnull', True)), fields=('chave', 'campo'), name='um_unico_valor_vigente_por_campo')],
            },
        ),
        migrations.RunPython(semear, limpar),
    ]
