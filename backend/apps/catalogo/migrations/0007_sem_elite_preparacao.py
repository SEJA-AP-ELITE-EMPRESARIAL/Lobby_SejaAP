"""
O ELITE PREPARAÇÃO sai do catálogo.

A DIFERENÇA EM RELAÇÃO À REMOÇÃO DA `0005`

Os seis produtos removidos na migração anterior estavam em categorias TRAVADAS:
nunca abriram no lobby, então era impossível existir venda deles. Este não —
`prep` estava na Elite, que está ativa, e podia ser vendido desde que entrou.

Por isso duas coisas:

1. A conferência contra `ComprovanteVenda` (abaixo) **para** a migração se achar
   venda emitida, em vez de apagar.

2. A conferência NÃO é prova de que nunca vendeu. O comprovante só existe desde
   07/08/2026 (`vendas/0001_initial`); antes disso o navegador postava direto no
   n8n e este banco não guardava nada. O registro de vendas mora no n8n e no
   Omie, não aqui.

O QUE ISSO SIGNIFICA NA PRÁTICA

Se houver protocolo `PRE-…` emitido em produção, ele continua existindo lá fora e
passa a apontar para um produto que não está mais no catálogo. Duas coisas
seguram a interpretação dele:

- A `Vigencia` do `prep` é **fechada**, não apagada: a linha do tempo continua
  dizendo "ELITE PREPARAÇÃO, mensalidade 9.997,00, de … até 17/08/2026". É
  exatamente o que se precisa para ler um protocolo antigo.
- A sigla `PRE` fica **queimada**: ela não volta a ser usada por nenhum produto
  novo. O ELITE PRÉ segue com `EPR`, mesmo com `PRE` agora "livre" no índice —
  reaproveitá-la faria protocolos antigos apontarem para o produto errado, que é
  precisamente o que a unicidade de sigla existe para evitar.

A Elite fica com seis planos, e a escada de preço é refeita:

    PRÉ 2.997 · BASE 5.997 · PRO 12.997 · GESTÃO 19.997 · EVO 29.997 · CONSELHO 49.997
"""
from django.db import migrations

SLUG = "prep"
ORDEM_ELITE = ["pre", "base", "pro", "gestao", "evo", "conselho"]


def remover(apps, schema_editor):
    from django.utils import timezone

    Produto = apps.get_model("catalogo", "Produto")
    Vigencia = apps.get_model("catalogo", "Vigencia")
    ComprovanteVenda = apps.get_model("vendas", "ComprovanteVenda")

    agora = timezone.now()
    produto = Produto.objects.filter(categoria__slug="elite", slug=SLUG).first()
    if produto is None:
        return

    vendido = ComprovanteVenda.objects.filter(
        valores__categoria_id="elite", valores__produto_id=SLUG
    ).exists()
    if vendido:
        raise RuntimeError(
            'O ELITE PREPARAÇÃO tem venda emitida neste banco: apagá-lo deixaria o '
            "protocolo dela sem produto. Tire este produto da migração e trate a "
            "saída de outra forma (categoria travada, ou um campo `ativo`)."
        )

    # Fecha antes de apagar: o histórico continua contando que ele existiu, por
    # quanto, e até quando — é o que permite ler um protocolo `PRE-…` antigo.
    Vigencia.objects.filter(chave=SLUG, vigente_ate__isnull=True).update(vigente_ate=agora)

    # A exceção de cobrança dele, se houver, sai junto (CASCADE cuidaria disso,
    # mas o fechamento da vigência precisa acontecer antes).
    produto.delete()

    for posicao, slug in enumerate(ORDEM_ELITE):
        Produto.objects.filter(categoria__slug="elite", slug=slug).update(ordem=posicao)


def desfazer(apps, schema_editor):
    """Não recria o produto.

    Recriá-lo aqui produziria uma linha com id novo e uma segunda vigência aberta
    para a mesma chave — o índice parcial recusaria, e o que passasse seria pior
    do que não ter. Para trazê-lo de volta, reverta até a `0002` (a semente) ou
    crie de novo pelo /admin.
    """
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("catalogo", "0006_valor_de_referencia_da_apn"),
        ("vendas", "0001_initial"),
    ]
    operations = [migrations.RunPython(remover, desfazer)]
