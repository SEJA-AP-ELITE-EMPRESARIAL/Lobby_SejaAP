"""
O teste que justifica a migração inteira.

`CATALOGO_EM_PRODUCAO` abaixo é a transcrição literal do array `CATS` de
`git show HEAD:index.html`, linhas 503-533 — o catálogo que os consultores estão
usando agora. O teste compara, campo a campo, o JSON que este backend serve com
aquele literal.

Se este teste passa, a troca do KV pelo Postgres é invisível para o front. Se
falha, alguém quebrou a cotação em campo e o erro aparece aqui, não na mão do
consultor.

Não "conserte" este arquivo copiando a saída do serializer. Ele só tem valor
enquanto for cópia independente da verdade de produção.
"""
from django.test import TestCase
from rest_framework.test import APIClient

CATALOGO_EM_PRODUCAO = [
    {
        "id": "elite",
        "name": "Elite",
        "icon": "diamond",
        "color": "purple",
        "desc": "Consultoria individual contínua — os planos Elite da Seja AP.",
        "products": [
            {
                "id": "prep", "name": "ELITE PREPARAÇÃO", "sigla": "PRE",
                "desc": "O plano do Início", "duration": "12 meses",
                "icon": "rocket_launch", "price": 9997 * 12,
                "monthly": 9997, "recurring": True, "vigencia": 12,
            },
            {
                "id": "pro", "name": "ELITE PRO", "sigla": "PRO",
                "desc": "O plano da Estrutura", "duration": "12 meses",
                "icon": "workspace_premium", "price": 12997 * 12,
                "monthly": 12997, "recurring": True, "vigencia": 12,
            },
            {
                "id": "gestao", "name": "ELITE GESTÃO", "sigla": "GES",
                "desc": "O plano da Escala", "duration": "12 meses",
                "icon": "insights", "price": 19997 * 12,
                "monthly": 19997, "recurring": True, "vigencia": 12,
            },
            {
                "id": "evo", "name": "ELITE EVO", "sigla": "EVO",
                "desc": "O plano do Legado", "duration": "12 meses",
                "icon": "diamond", "price": 29997 * 12,
                "monthly": 29997, "recurring": True, "vigencia": 12,
            },
        ],
    },
    {
        "id": "treinamentos",
        "name": "Treinamentos",
        "icon": "school",
        "color": "gold",
        "desc": "Imersões e trilhas de desenvolvimento de líderes e equipes comerciais.",
        "locked": True,
        "products": [
            {
                "id": "t1", "name": "Imersão Gestão 360", "sigla": "IMG",
                "desc": "Imersão presencial de 3 dias", "duration": "3 dias",
                "icon": "groups", "price": 18000,
            },
            {
                "id": "t2", "name": "Trilha Líder Elite", "sigla": "TLE",
                "desc": "Programa de formação de líderes", "duration": "6 meses",
                "icon": "military_tech", "price": 24000,
            },
            {
                "id": "t3", "name": "Vendas de Alta Performance", "sigla": "VAP",
                "desc": "Treinamento intensivo de vendas", "duration": "2 meses",
                "icon": "trending_up", "price": 12000,
            },
        ],
    },
    {
        "id": "apn",
        "name": "APN",
        "icon": "rocket_launch",
        "color": "blue",
        "flow": "apn",
        "sigla": "APN",
        "desc": "Aceleração de Performance de Negócios — valor definido na negociação.",
        "products": [],
    },
    {
        "id": "palestras",
        "name": "Palestras",
        "icon": "campaign",
        "color": "green",
        "desc": "Palestras in company e eventos de alto impacto.",
        "locked": True,
        "products": [
            {
                "id": "pl1", "name": "Palestra In Company", "sigla": "PIC",
                "desc": "Palestra exclusiva na empresa", "duration": "1 evento",
                "icon": "record_voice_over", "price": 15000,
            },
            {
                "id": "pl2", "name": "Palestra Magna", "sigla": "PMA",
                "desc": "Palestra para grandes públicos", "duration": "1 evento",
                "icon": "stadium", "price": 25000,
            },
            {
                "id": "pl3", "name": "Ciclo de Palestras Anual", "sigla": "CPA",
                "desc": "Programa anual de palestras", "duration": "12 meses",
                "icon": "event", "price": 80000,
            },
        ],
    },
]


class ContratoDoCatalogoTest(TestCase):
    """O JSON servido tem que ser o mesmo que o front recebe hoje."""

    def setUp(self):
        self.client = APIClient()

    def test_get_publico_devolve_o_catalogo_de_producao(self):
        resposta = self.client.get("/api/catalogo")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["cats"], CATALOGO_EM_PRODUCAO)

    def test_ordem_das_categorias_e_a_do_lobby(self):
        """O front faz `CATS.map` direto, sem ordenar (`index.html:1284`)."""
        cats = self.client.get("/api/catalogo").json()["cats"]
        self.assertEqual(
            [c["id"] for c in cats], ["elite", "treinamentos", "apn", "palestras"]
        )

    def test_get_e_anonimo(self):
        """Consultor sem login precisa conseguir cotar. É o ponto do app."""
        resposta = APIClient().get("/api/catalogo")
        self.assertEqual(resposta.status_code, 200)

    def test_resposta_nunca_e_cacheavel(self):
        """Atrás da Cloudflare, preço cacheado na borda é venda com valor errado."""
        resposta = self.client.get("/api/catalogo")
        self.assertEqual(resposta["Cache-Control"], "no-store")

    def test_envelope_tem_atualizado_em_e_origem(self):
        corpo = self.client.get("/api/catalogo").json()
        self.assertIn("atualizadoEm", corpo)
        self.assertIn("origem", corpo)
        # Sem publicação ainda: null, não string vazia. O `admin.html:289` joga
        # isto num `new Date(...)` e "" viraria "Invalid Date" na tela.
        self.assertIsNone(corpo["atualizadoEm"])

    def test_numeros_sao_numeros_e_nao_strings(self):
        """DecimalField do DRF serializa como string por padrão.

        Isso quebraria o dirty-check do admin (`admin.html:209`, comparação com
        `!==`) e mandaria `valor_tabela` como texto para o n8n
        (`index.html:1039`).
        """
        cats = self.client.get("/api/catalogo").json()["cats"]
        elite = cats[0]["products"][0]
        for campo in ("price", "monthly", "vigencia"):
            self.assertIsInstance(elite[campo], int, f"{campo} não é número")

    def test_produto_avulso_nao_tem_campos_de_recorrencia(self):
        """`recurring` ausente é como o front distingue avulso de recorrente."""
        cats = self.client.get("/api/catalogo").json()["cats"]
        avulso = cats[1]["products"][0]
        for campo in ("monthly", "recurring", "vigencia"):
            self.assertNotIn(campo, avulso)

    def test_categoria_destravada_nao_emite_locked(self):
        """O front testa `!!c.locked`; emitir `false` funcionaria, mas muda o
        contrato à toa. Elite e APN estão destravadas."""
        cats = self.client.get("/api/catalogo").json()["cats"]
        self.assertNotIn("locked", cats[0])
        self.assertNotIn("locked", cats[2])

    def test_apn_carrega_flow_e_sigla(self):
        """Sem `flow`, o front manda o consultor para o wizard errado.

        Este é o campo que o normalizador do KV descartava na escrita — a APN só
        o tinha porque vinha de uma constante que nunca passava por lá.
        """
        cats = self.client.get("/api/catalogo").json()["cats"]
        apn = next(c for c in cats if c["id"] == "apn")
        self.assertEqual(apn["flow"], "apn")
        self.assertEqual(apn["sigla"], "APN")
        self.assertEqual(apn["products"], [])

    def test_preco_de_recorrente_e_sempre_mensalidade_vezes_vigencia(self):
        """Se os dois discordarem, o consultor não consegue fechar a venda:
        o total exibido vem de `monthly × vigencia` e o cronograma de `price`."""
        cats = self.client.get("/api/catalogo").json()["cats"]
        for produto in cats[0]["products"]:
            self.assertEqual(
                produto["price"], produto["monthly"] * produto["vigencia"]
            )
