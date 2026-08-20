"""
O ELITE BASE passa na frente do ELITE PRÉ na tela.

O QUE MUDA, E O QUE NÃO MUDA

Só o campo `ordem` dos dois. Nada de preço, sigla, vigência ou política de
cobrança é tocado — esta migração não altera uma linha de dinheiro, e por isso
não abre nem fecha `Vigencia` nenhuma. A linha do tempo registra o que foi
cobrado, não a posição do card no lobby.

POR QUE A ESCADA DE PREÇO DEIXOU DE VALER AQUI

A `0005` estabeleceu a ordem do catálogo como a escada de preço, do mais barato
ao mais caro, e a `0007` a refez quando o ELITE PREPARAÇÃO saiu:

    PRÉ 2.997 · BASE 5.997 · PRO 12.997 · GESTÃO 19.997 · EVO 29.997 · CONSELHO 49.997

A ordem pedida pela diretoria em 20/08/2026 quebra essa regra no primeiro
degrau, de propósito: o BASE é o plano de entrada que a operação oferece
primeiro, e o PRÉ existe para quem não fecha o BASE. Deixar o PRÉ no topo fazia
o consultor abrir a conversa pelo plano mais barato.

    BASE 5.997 · PRÉ 2.997 · PRO 12.997 · GESTÃO 19.997 · EVO 29.997 · CONSELHO 49.997

Do PRO para baixo a escada continua valendo. Quem for mexer nisso depois: a ordem
NÃO é derivada do preço em lugar nenhum do código — é o campo `ordem`, e só ele.

ISTO PODE SER DESFEITO PELO /admin

`ordem` é editável pela diretoria sem deploy (`produtos.py`). Se a tela publicar
outra ordem depois desta migração, é a tela que vale — como em qualquer outro
campo do catálogo. Esta migração só define de onde o banco parte.
"""
from django.db import migrations

ORDEM_NOVA = ["base", "pre", "pro", "gestao", "evo", "conselho"]
ORDEM_ANTIGA = ["pre", "base", "pro", "gestao", "evo", "conselho"]


def _reordena(apps, ordem):
    Produto = apps.get_model("catalogo", "Produto")
    for posicao, slug in enumerate(ordem):
        Produto.objects.filter(categoria__slug="elite", slug=slug).update(
            ordem=posicao
        )


def aplicar(apps, schema_editor):
    _reordena(apps, ORDEM_NOVA)


def desfazer(apps, schema_editor):
    """Devolve a escada de preço da `0007`.

    Diferente da `0007`, aqui desfazer é seguro e completo: nada foi criado nem
    apagado, então recolocar as posições restaura exatamente o estado anterior.
    """
    _reordena(apps, ORDEM_ANTIGA)


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0007_sem_elite_preparacao")]
    operations = [migrations.RunPython(aplicar, desfazer)]
