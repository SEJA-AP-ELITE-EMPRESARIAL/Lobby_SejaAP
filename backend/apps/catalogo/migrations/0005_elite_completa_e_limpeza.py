"""
A Elite ganha três planos; Treinamentos e Palestras perdem os produtos.

OS TRÊS PLANOS NOVOS

Vieram da diretoria em 17/08/2026, com valor de mensalidade. A ordem do catálogo
passa a ser a escada de preço, o que obriga a reordenar os quatro que já
existiam — a ordem RELATIVA entre eles não muda, só a posição na lista:

    PRÉ 2.997 · BASE 5.997 · PREPARAÇÃO 9.997 · PRO 12.997
    GESTÃO 19.997 · EVO 29.997 · CONSELHO 49.997

`sigla` do ELITE PRÉ é `EPR`, e não `PRE`: `PRE` é do ELITE PREPARAÇÃO desde a
semente, e sigla publicada não se troca — ela é o `SSS` do protocolo de vendas já
fechadas, e trocá-la faria protocolos antigos apontarem para o produto errado.

A REMOÇÃO DOS PRODUTOS DE TREINAMENTOS E PALESTRAS

Estes seis produtos vieram da semente inicial como transcrição do que estava em
produção, dentro de categorias `travada=True` ("Em implementação"). Nunca foram
vendidos: categoria travada não abre no lobby, então não existe protocolo
apontando para eles.

É por isso que apagar aqui não contradiz a regra de que **produto não se apaga**
(`produtos.py`, `on_delete=PROTECT`). A regra existe para não deixar protocolo
órfão; sem venda, não há protocolo. Ainda assim a conferência abaixo é feita em
tempo de execução, contra os comprovantes emitidos — se algum dia um deles tiver
sido vendido, a migração **para** em vez de apagar.

As categorias FICAM, e travadas. Elas seguem aparecendo no lobby como "Em
implementação", que é o que se quer: o consultor vê que existem e que ainda não
abriram. Só a tabela de preços delas fica vazia, esperando os produtos de verdade.

AS VIGÊNCIAS DOS REMOVIDOS SÃO FECHADAS, NÃO APAGADAS

A linha do tempo é registro do que aconteceu, e esses produtos estiveram no
catálogo. Fechar (`vigente_ate`) conta a história certa: "valeu de tal data até
tal data". Apagar seria reescrever o passado — o oposto do que a tabela existe
para fazer.
"""
from decimal import Decimal

from django.db import migrations

# Ordem final da Elite, do mais barato ao mais caro. Os quatro existentes
# aparecem aqui só para receber a posição nova.
ORDEM_ELITE = ["pre", "base", "prep", "pro", "gestao", "evo", "conselho"]

NOVOS = [
    {
        "slug": "pre",
        "nome": "ELITE PRÉ",
        # `PRE` já é do ELITE PREPARAÇÃO — ver o cabeçalho.
        "sigla": "EPR",
        "descricao": "O plano do Primeiro Passo",
        "duracao": "12 meses",
        "icone": "start",
        "recorrente": True,
        "mensalidade": Decimal("2997"),
        "vigencia_meses": 12,
    },
    {
        "slug": "base",
        "nome": "ELITE BASE",
        "sigla": "BAS",
        "descricao": "O plano da Fundação",
        "duracao": "12 meses",
        "icone": "foundation",
        "recorrente": True,
        "mensalidade": Decimal("5997"),
        "vigencia_meses": 12,
    },
    {
        "slug": "conselho",
        "nome": "ELITE CONSELHO",
        "sigla": "CON",
        "descricao": "O plano do Conselho Consultivo",
        "duracao": "12 meses",
        "icone": "groups",
        "recorrente": True,
        "mensalidade": Decimal("49997"),
        "vigencia_meses": 12,
    },
]

# Os produtos da semente que nunca abriram para venda.
A_REMOVER = {
    "treinamentos": ["t1", "t2", "t3"],
    "palestras": ["pl1", "pl2", "pl3"],
}


def aplicar(apps, schema_editor):
    from django.utils import timezone

    Categoria = apps.get_model("catalogo", "Categoria")
    Produto = apps.get_model("catalogo", "Produto")
    Vigencia = apps.get_model("catalogo", "Vigencia")
    ComprovanteVenda = apps.get_model("vendas", "ComprovanteVenda")

    agora = timezone.now()
    elite = Categoria.objects.get(slug="elite")

    for dados in NOVOS:
        produto, criado = Produto.objects.get_or_create(
            categoria=elite,
            slug=dados["slug"],
            defaults={**dados, "ordem": ORDEM_ELITE.index(dados["slug"])},
        )
        if not criado:
            continue
        # Abre a vigência do valor, como faria uma publicação pelo /admin. Sem
        # isto o plano novo apareceria no histórico sem origem.
        Vigencia.objects.bulk_create([
            Vigencia(
                chave=produto.slug,
                rotulo=produto.nome,
                campo="mensalidade",
                valor=f"{produto.mensalidade:.2f}",
                vigente_de=agora,
            ),
            Vigencia(
                chave=produto.slug,
                rotulo=produto.nome,
                campo="vigencia_meses",
                valor=str(produto.vigencia_meses),
                vigente_de=agora,
            ),
        ])

    # A escada de preço vale para todos, inclusive os quatro que já existiam.
    for posicao, slug in enumerate(ORDEM_ELITE):
        Produto.objects.filter(categoria=elite, slug=slug).update(ordem=posicao)

    # ----- remoção dos produtos que nunca abriram -------------------------
    for categoria_slug, slugs in A_REMOVER.items():
        produtos = Produto.objects.filter(
            categoria__slug=categoria_slug, slug__in=slugs
        )
        for produto in produtos:
            vendido = ComprovanteVenda.objects.filter(
                valores__produto_id=produto.slug,
                valores__categoria_id=categoria_slug,
            ).exists()
            if vendido:
                raise RuntimeError(
                    f'O produto "{categoria_slug}/{produto.slug}" tem venda emitida '
                    "e não pode ser apagado — o protocolo dela ficaria órfão. "
                    "Remova este produto da lista desta migração."
                )
        # Fecha a linha do tempo antes de a linha sumir: o histórico continua
        # contando que eles existiram, e até quando.
        Vigencia.objects.filter(
            chave__in=[p.slug for p in produtos], vigente_ate__isnull=True
        ).update(vigente_ate=agora)
        produtos.delete()


def desfazer(apps, schema_editor):
    """Tira os planos novos. Não ressuscita os removidos.

    Reverter esta migração devolve o catálogo a sete produtos, não a dez: os seis
    de Treinamentos e Palestras estão na semente da `0002`, e desfazer até lá é
    quem os traz de volta. Recriá-los aqui produziria linhas com id novo e
    vigências duplicadas — pior do que não ter.
    """
    Produto = apps.get_model("catalogo", "Produto")
    Vigencia = apps.get_model("catalogo", "Vigencia")
    slugs = [d["slug"] for d in NOVOS]
    Produto.objects.filter(categoria__slug="elite", slug__in=slugs).delete()
    Vigencia.objects.filter(chave__in=slugs).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalogo", "0004_politica_de_cobranca_e_vigencias"),
        # A conferência de venda emitida lê `vendas.ComprovanteVenda`.
        ("vendas", "0001_initial"),
    ]
    operations = [migrations.RunPython(aplicar, desfazer)]
