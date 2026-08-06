"""
A semente do catálogo.

FONTE DOS VALORES: `git show HEAD:index.html`, linhas 503-533 — ou seja, a
tabela de preços que está NO AR hoje e que os consultores estão usando.

Por que isso precisa estar escrito:

Existiam três tabelas candidatas quando esta migração foi escrita, e elas não
concordavam. A de produção (acima), a constante `CATALOGO_PADRAO` de
`functions/_lib/catalogo.js:14-43` (nunca publicada, mas idêntica em valor), e
um estado local de desenvolvimento em `.wrangler/state/v3/kv/` com dois
registros de 12/07/2026 — 01:13 e 01:17 — em que o ELITE PRO aparece como
14997 e depois 21000. Dois registros com 17 minutos de diferença, num namespace
de miniflare local, são alguém testando a tela de edição. Não são tabela de
preço, e não entram aqui.

A categoria APN também não vem do KV. Lá ela existia como registro com três
produtos (a1/AST, a2/APR, a3/ABL) que o `comCategoriasDeCodigo` escondia da API
— siglas que `docs/08_ANDAMENTO.md:20-21` já tinha aposentado. Como este banco
nasce da semente e não de uma importação, esses produtos mortos simplesmente
não existem. Se um dia alguém importar o KV antigo, precisa saber disso.
"""
from decimal import Decimal

from django.db import migrations

CATEGORIAS = [
    {
        "slug": "elite",
        "nome": "Elite",
        "icone": "diamond",
        "cor": "purple",
        "descricao": "Consultoria individual contínua — os planos Elite da Seja AP.",
        "travada": False,
        "fluxo": "",
        "sigla": "",
        "ordem": 0,
        "produtos": [
            {
                "slug": "prep",
                "nome": "ELITE PREPARAÇÃO",
                "sigla": "PRE",
                "descricao": "O plano do Início",
                "duracao": "12 meses",
                "icone": "rocket_launch",
                "recorrente": True,
                "mensalidade": Decimal("9997"),
                "vigencia_meses": 12,
            },
            {
                "slug": "pro",
                "nome": "ELITE PRO",
                "sigla": "PRO",
                "descricao": "O plano da Estrutura",
                "duracao": "12 meses",
                "icone": "workspace_premium",
                "recorrente": True,
                "mensalidade": Decimal("12997"),
                "vigencia_meses": 12,
            },
            {
                "slug": "gestao",
                "nome": "ELITE GESTÃO",
                "sigla": "GES",
                "descricao": "O plano da Escala",
                "duracao": "12 meses",
                "icone": "insights",
                "recorrente": True,
                "mensalidade": Decimal("19997"),
                "vigencia_meses": 12,
            },
            {
                "slug": "evo",
                "nome": "ELITE EVO",
                "sigla": "EVO",
                "descricao": "O plano do Legado",
                "duracao": "12 meses",
                "icone": "diamond",
                "recorrente": True,
                "mensalidade": Decimal("29997"),
                "vigencia_meses": 12,
            },
        ],
    },
    {
        "slug": "treinamentos",
        "nome": "Treinamentos",
        "icone": "school",
        "cor": "gold",
        "descricao": "Imersões e trilhas de desenvolvimento de líderes e equipes comerciais.",
        "travada": True,
        "fluxo": "",
        "sigla": "",
        "ordem": 1,
        "produtos": [
            {
                "slug": "t1",
                "nome": "Imersão Gestão 360",
                "sigla": "IMG",
                "descricao": "Imersão presencial de 3 dias",
                "duracao": "3 dias",
                "icone": "groups",
                "recorrente": False,
                "valor": Decimal("18000"),
            },
            {
                "slug": "t2",
                "nome": "Trilha Líder Elite",
                "sigla": "TLE",
                "descricao": "Programa de formação de líderes",
                "duracao": "6 meses",
                "icone": "military_tech",
                "recorrente": False,
                "valor": Decimal("24000"),
            },
            {
                "slug": "t3",
                "nome": "Vendas de Alta Performance",
                "sigla": "VAP",
                "descricao": "Treinamento intensivo de vendas",
                "duracao": "2 meses",
                "icone": "trending_up",
                "recorrente": False,
                "valor": Decimal("12000"),
            },
        ],
    },
    {
        # Categoria de fluxo próprio: sem tabela de preços, o consultor informa o
        # valor da venda. `flow` é o que o front lê para mandar a pessoa ao wizard
        # curto (`index.html:597`), e `sigla` alimenta o protocolo no lugar da
        # sigla do produto, que aqui não existe.
        "slug": "apn",
        "nome": "APN",
        "icone": "rocket_launch",
        "cor": "blue",
        "descricao": "Aceleração de Performance de Negócios — valor definido na negociação.",
        "travada": False,
        "fluxo": "apn",
        "sigla": "APN",
        "ordem": 2,
        "produtos": [],
    },
    {
        "slug": "palestras",
        "nome": "Palestras",
        "icone": "campaign",
        "cor": "green",
        "descricao": "Palestras in company e eventos de alto impacto.",
        "travada": True,
        "fluxo": "",
        "sigla": "",
        "ordem": 3,
        "produtos": [
            {
                "slug": "pl1",
                "nome": "Palestra In Company",
                "sigla": "PIC",
                "descricao": "Palestra exclusiva na empresa",
                "duracao": "1 evento",
                "icone": "record_voice_over",
                "recorrente": False,
                "valor": Decimal("15000"),
            },
            {
                "slug": "pl2",
                "nome": "Palestra Magna",
                "sigla": "PMA",
                "descricao": "Palestra para grandes públicos",
                "duracao": "1 evento",
                "icone": "stadium",
                "recorrente": False,
                "valor": Decimal("25000"),
            },
            {
                "slug": "pl3",
                "nome": "Ciclo de Palestras Anual",
                "sigla": "CPA",
                "descricao": "Programa anual de palestras",
                "duracao": "12 meses",
                "icone": "event",
                "recorrente": False,
                "valor": Decimal("80000"),
            },
        ],
    },
]


def semear(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Produto = apps.get_model("catalogo", "Produto")

    for original in CATEGORIAS:
        # Cópia: `pop` na constante do módulo a esvaziaria, e a segunda execução
        # no mesmo processo (testes, `migrate` repetido) quebraria com KeyError.
        dados = dict(original)
        produtos = dados.pop("produtos")
        # `get_or_create` e não `create`: a migração precisa ser segura de rodar
        # num banco que já tem catálogo — por exemplo, ao recriar o ambiente de
        # um desenvolvedor sem apagar o volume.
        categoria, criada = Categoria.objects.get_or_create(
            slug=dados["slug"], defaults=dados
        )
        if not criada:
            continue
        for ordem, produto in enumerate(produtos):
            Produto.objects.create(categoria=categoria, ordem=ordem, **produto)


def remover(apps, schema_editor):
    """Desfaz a semente. Só remove o que ela plantou, pelo slug."""
    Categoria = apps.get_model("catalogo", "Categoria")
    Produto = apps.get_model("catalogo", "Produto")
    slugs = [c["slug"] for c in CATEGORIAS]
    Produto.objects.filter(categoria__slug__in=slugs).delete()
    Categoria.objects.filter(slug__in=slugs).delete()


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0001_initial")]
    operations = [migrations.RunPython(semear, remover)]
