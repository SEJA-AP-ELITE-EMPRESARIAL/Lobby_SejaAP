"""
A escolha de departamento de hoje vira a escolha de CADA categoria (TSK-1059).

Até aqui havia uma escolha só — lista completa ou departamento fixo — para o
Elite e a APN juntos (e, na prática, para toda categoria de tabela, que usa o
mesmo cartão). Desde a TSK-1059 cada categoria tem a sua.

O QUE ESTA MIGRATION GARANTE

O consultor vê no dia do deploy exatamente o que via na véspera: a última
gravação de antes é copiada para cada categoria, com o MESMO modo, o MESMO
departamento fixo, o MESMO autor e a MESMA data — foi aquela pessoa, naquele
dia, quem decidiu, e a aba deve continuar dizendo isso. `copiada_de` aponta a
original, para o histórico mostrar uma decisão só, e não uma por categoria.

Sem gravação nenhuma antes, nada é criado: sem linha vale a lista completa, que
é o mesmo que valia.

REVERSÍVEL

Desfazer apaga as linhas por categoria. Se todas as categorias estiverem com a
mesma escolha e ela for diferente da última gravação de antes (o /admin antigo
mudou depois do deploy, para todas), ela vira uma gravação sem categoria antes
de as linhas sumirem — e o consultor não percebe a volta. Com escolhas
DIFERENTES por categoria não há uma só que represente todas: volta a valer a
última gravação de antes do deploy, e o que foi configurado por categoria se
perde.
"""
from django.db import migrations


def _efetiva(linha):
    return (linha.modo, linha.departamento_id) if linha is not None else ("lista", None)


def copia_para_cada_categoria(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Configuracao = apps.get_model("departamentos", "ConfiguracaoDepartamento")

    original = (
        Configuracao.objects.filter(categoria__isnull=True).order_by("-criado_em", "-id").first()
    )
    if original is None:
        return

    for categoria in Categoria.objects.all():
        if Configuracao.objects.filter(categoria=categoria).exists():
            continue
        copia = Configuracao.objects.create(
            modo=original.modo,
            departamento_id=original.departamento_id,
            categoria=categoria,
            copiada_de=original,
            autor_id=original.autor_id,
            autor_email=original.autor_email,
        )
        # `auto_now_add` ignora o valor do create; só o update carimba a data
        # da decisão original.
        Configuracao.objects.filter(pk=copia.pk).update(criado_em=original.criado_em)


def volta_a_escolha_unica(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Configuracao = apps.get_model("departamentos", "ConfiguracaoDepartamento")

    por_categoria = Configuracao.objects.filter(categoria__isnull=False)
    if por_categoria.exists():
        ultimas = {
            categoria.pk: por_categoria.filter(categoria=categoria).order_by("-criado_em", "-id").first()
            for categoria in Categoria.objects.all()
        }
        escolhas = {_efetiva(linha) for linha in ultimas.values()}
        antiga = (
            Configuracao.objects.filter(categoria__isnull=True).order_by("-criado_em", "-id").first()
        )
        if len(escolhas) == 1 and escolhas != {_efetiva(antiga)}:
            (modo, departamento_id), = escolhas
            mais_recente = por_categoria.order_by("-criado_em", "-id").first()
            Configuracao.objects.create(
                modo=modo,
                departamento_id=departamento_id,
                categoria=None,
                autor_id=mais_recente.autor_id,
                autor_email=mais_recente.autor_email,
            )
    # As cópias apontam para a original com SET_NULL; apagar as por categoria
    # não toca nas de antes.
    por_categoria.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("departamentos", "0003_configuracao_por_categoria"),
    ]

    operations = [
        migrations.RunPython(copia_para_cada_categoria, volta_a_escolha_unica),
    ]
