"""
O dia do vencimento ganha liga/desliga: `PoliticaCobranca.dia_fixo` (TSK-1077).

O QUE ESTA MIGRATION GARANTE

Nenhuma venda muda de data no dia do deploy: toda regra que já existe (a geral,
a de cada categoria e cada exceção por produto) nasce com o dia fixo LIGADO,
que é exatamente o comportamento de hoje. O padrão fica também no banco
(`db_default`), para o backend de antes, que não conhece a coluna, continuar
criando exceção.

A linha do tempo de cada regra abre o `dia_fixo` em "sim", para a primeira
mudança de verdade aparecer como "ligado → desligado". A data é a do início da
regra — a vigência aberta mais antiga dela, ou a criação da linha —, pelo mesmo
motivo das sementes da 0004 e da 0016: carimbar com o dia do deploy diria que
todas mudaram hoje. Autor nulo: não houve pessoa, houve migração. O histórico
unificado não mostra esta semente como evento (`integracao/historico.py`).

REVERSÍVEL

Desfazer apaga a coluna e a linha do tempo do `dia_fixo` inteira (a semente e o
que vier depois), que não existiam antes desta migration. Regra que estiver com
o dia fixo DESLIGADO volta a cobrar no dia do vencimento guardado — é o preço de
voltar para um modelo que só conhece o dia fixo.
"""
from django.db import migrations, models

PREFIXO = "categoria:"
# O tamanho de `Vigencia.rotulo`. Produto pode ter nome de 120, e "NOME · cobrança"
# não caberia: corta-se o nome, como em `vigencias.cabe` (copiado: é história).
ROTULO_MAX = 120
# Os campos de cobrança que já tinham linha do tempo antes desta migration.
CAMPOS = ("dia_vencimento", "primeiro_vencimento", "entrada_prazo_dias")


def _rotulo(nome):
    sufixo = " · cobrança"
    if len(nome) + len(sufixo) <= ROTULO_MAX:
        return nome + sufixo
    return nome[: ROTULO_MAX - len(sufixo) - 1] + "…" + sufixo


def _chave_e_rotulo(politica):
    # O mesmo que `vigencias.registrar_politica`, copiado: migration é história.
    if politica.geral:
        return "geral", "Política de cobrança"
    if politica.categoria_id:
        return PREFIXO + politica.categoria.slug, _rotulo(politica.categoria.nome)
    return politica.produto.slug, _rotulo(politica.produto.nome)


def semear(apps, schema_editor):
    PoliticaCobranca = apps.get_model("catalogo", "PoliticaCobranca")
    Vigencia = apps.get_model("catalogo", "Vigencia")
    for politica in PoliticaCobranca.objects.select_related("categoria", "produto"):
        chave, rotulo = _chave_e_rotulo(politica)
        if Vigencia.objects.filter(chave=chave, campo="dia_fixo", vigente_ate__isnull=True).exists():
            continue
        abertas = Vigencia.objects.filter(chave=chave, campo__in=CAMPOS, vigente_ate__isnull=True)
        inicio = min([v.vigente_de for v in abertas] + [politica.criado_em])
        Vigencia.objects.create(
            chave=chave,
            rotulo=rotulo,
            campo="dia_fixo",
            valor="sim" if politica.dia_fixo else "não",
            vigente_de=inicio,
            autor=None,
            autor_email="",
        )


def limpar(apps, schema_editor):
    apps.get_model("catalogo", "Vigencia").objects.filter(campo="dia_fixo").delete()


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0016_semente_da_trava_de_categoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='politicacobranca',
            name='dia_fixo',
            field=models.BooleanField(db_default=True, default=True, help_text='Ligado: as parcelas vencem no dia do vencimento. Desligado: no dia do mês em que a venda foi feita (no último dia, em mês que não tem aquele dia), e o dia do vencimento e a primeira parcela não valem.', verbose_name='dia do vencimento fixo'),
        ),
        migrations.AlterField(
            model_name='vigencia',
            name='campo',
            field=models.CharField(choices=[('mensalidade', 'Mensalidade'), ('valor', 'Valor à vista'), ('valor_referencia', 'Valor de referência'), ('vigencia_meses', 'Vigência (meses)'), ('dia_vencimento', 'Dia do vencimento'), ('primeiro_vencimento', 'Primeira parcela'), ('entrada_prazo_dias', 'Prazo da entrada (dias)'), ('dia_fixo', 'Dia do vencimento fixo'), ('travada', 'Fora do Lobby (em implementação)')], max_length=30, verbose_name='campo'),
        ),
        migrations.RunPython(semear, limpar),
    ]
