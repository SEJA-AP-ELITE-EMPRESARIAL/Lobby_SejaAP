"""
Abre a linha do tempo de `Categoria.travada` com o estado de hoje (TSK-1059).

Desde a TSK-1059 liberar ou tirar uma categoria do Lobby deixa `Vigencia`, com
autor. Sem esta semente, a primeira mudança apareceria sem o "de": o histórico
diria "Treinamentos: no Lobby", e não "fora do Lobby → no Lobby".

A data é a de criação da categoria, pelo mesmo motivo da semente da 0004: é a
melhor aproximação honesta de "desde quando", e carimbar com o dia do deploy
diria que todas mudaram hoje. Autor nulo: não houve pessoa, houve migração.

Reversível: desfazer apaga a linha do tempo da trava inteira (a semente e o que
vier depois), que não existia antes desta migration.
"""
from django.db import migrations

PREFIXO = "categoria:"


def semear(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Vigencia = apps.get_model("catalogo", "Vigencia")
    for categoria in Categoria.objects.all():
        chave = PREFIXO + categoria.slug
        if Vigencia.objects.filter(chave=chave, campo="travada", vigente_ate__isnull=True).exists():
            continue
        Vigencia.objects.create(
            chave=chave,
            rotulo=categoria.nome,
            campo="travada",
            valor="sim" if categoria.travada else "não",
            vigente_de=categoria.criado_em,
            autor=None,
            autor_email="",
        )


def limpar(apps, schema_editor):
    apps.get_model("catalogo", "Vigencia").objects.filter(
        chave__startswith=PREFIXO, campo="travada"
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalogo", "0015_cada_categoria_com_a_regra_geral"),
    ]

    operations = [
        migrations.RunPython(semear, limpar),
    ]
