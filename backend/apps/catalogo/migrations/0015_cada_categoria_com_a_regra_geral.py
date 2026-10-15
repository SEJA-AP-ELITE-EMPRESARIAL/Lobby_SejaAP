"""
A regra de cobrança geral vira a regra de CADA categoria (TSK-1059).

Até aqui havia uma regra só, a geral, e as exceções por produto. Desde a
TSK-1059 cada categoria tem a sua (`PoliticaCobranca.categoria`), e a geral fica
como a regra com que uma categoria nova começa.

O QUE ESTA MIGRATION GARANTE

Nenhuma venda muda de data no dia do deploy: cada categoria nasce com
exatamente os três valores da geral (dia do vencimento, primeira parcela, prazo
da entrada). As exceções por produto não são tocadas e continuam vencendo.

A linha do tempo de cada categoria abre com a MESMA data da vigência da geral
que estava aberta — e não com a data do deploy. É a resposta certa para "qual
era a regra da Elite em setembro": a da geral, que era a que valia para ela. O
rótulo diz que é cópia, e o autor fica nulo, como em toda semente: não houve
pessoa, houve migração.

REVERSÍVEL

Desfazer apaga as regras de categoria e as vigências delas. Se todas as
categorias estiverem com a mesma regra, ela é gravada de volta na geral antes —
assim, desfazer depois de uma mudança feita pelo /admin antigo (que aplica a
mesma regra a todas) não muda nenhuma data. Se as categorias tiverem regras
DIFERENTES, não há uma só que represente todas: a geral fica como está, e as
categorias que divergiam voltam a seguir a geral. Isso perde o que foi
configurado por categoria — é o preço de voltar para um modelo que só sabe
guardar uma regra.
"""
from django.db import migrations
from django.utils import timezone

PREFIXO = "categoria:"
CAMPOS = ("dia_vencimento", "primeiro_vencimento", "entrada_prazo_dias")
# O que `PoliticaCobranca.geral_atual()` criaria num banco sem a linha geral.
# Literal, e não importado do modelo: migration é história.
PADRAO = {"dia_vencimento": 15, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}


def copia_a_geral(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    PoliticaCobranca = apps.get_model("catalogo", "PoliticaCobranca")
    Vigencia = apps.get_model("catalogo", "Vigencia")

    geral = PoliticaCobranca.objects.filter(geral=True).first()
    regra = {campo: getattr(geral, campo) for campo in CAMPOS} if geral else dict(PADRAO)
    abertas_da_geral = {
        v.campo: v
        for v in Vigencia.objects.filter(chave="geral", campo__in=CAMPOS, vigente_ate__isnull=True)
    }
    agora = timezone.now()

    for categoria in Categoria.objects.all():
        if PoliticaCobranca.objects.filter(categoria=categoria).exists():
            continue
        PoliticaCobranca.objects.create(geral=False, produto=None, categoria=categoria, **regra)

        chave = PREFIXO + categoria.slug
        for campo in CAMPOS:
            if Vigencia.objects.filter(chave=chave, campo=campo, vigente_ate__isnull=True).exists():
                continue
            da_geral = abertas_da_geral.get(campo)
            Vigencia.objects.create(
                chave=chave,
                rotulo=f"{categoria.nome} · cobrança (copiada da regra geral)",
                campo=campo,
                valor=str(regra[campo]),
                vigente_de=da_geral.vigente_de if da_geral else agora,
                autor=None,
                autor_email="",
            )


def devolve_a_geral(apps, schema_editor):
    PoliticaCobranca = apps.get_model("catalogo", "PoliticaCobranca")
    Vigencia = apps.get_model("catalogo", "Vigencia")

    das_categorias = list(PoliticaCobranca.objects.filter(categoria__isnull=False))
    regras = {tuple(getattr(p, campo) for campo in CAMPOS) for p in das_categorias}
    geral = PoliticaCobranca.objects.filter(geral=True).first()
    if len(regras) == 1 and geral is not None:
        (unica,) = regras
        agora = timezone.now()
        for campo, valor in zip(CAMPOS, unica):
            if getattr(geral, campo) == valor:
                continue
            setattr(geral, campo, valor)
            # A linha do tempo da geral acompanha, para não dizer que a regra
            # antiga valeu até hoje.
            Vigencia.objects.filter(chave="geral", campo=campo, vigente_ate__isnull=True).update(
                vigente_ate=agora
            )
            Vigencia.objects.create(
                chave="geral",
                rotulo="Política de cobrança",
                campo=campo,
                valor=str(valor),
                vigente_de=agora,
                autor=None,
                autor_email="",
            )
        geral.save()

    PoliticaCobranca.objects.filter(categoria__isnull=False).delete()
    Vigencia.objects.filter(chave__startswith=PREFIXO, campo__in=CAMPOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalogo", "0014_politica_de_cobranca_por_categoria"),
    ]

    operations = [
        migrations.RunPython(copia_a_geral, devolve_a_geral),
    ]
