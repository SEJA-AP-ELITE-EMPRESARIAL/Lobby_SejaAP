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

POR QUE A COMPARAÇÃO É POR ID, E NÃO DA LISTA INTEIRA

A primeira versão fazia `assertEqual(cats, CATALOGO_EM_PRODUCAO)`. Isso confunde
duas coisas muito diferentes: ALTERAR um produto que está sendo vendido (o que
este arquivo existe para pegar) e ACRESCENTAR um produto novo (operação prevista,
feita no /django-admin/ — a Elite está recebendo produtos novos). Com a igualdade
de lista, a segunda derrubava a guarda da primeira, e o caminho de menor esforço
para voltar ao verde era colar a saída do serializer aqui — matando o teste.

Então: cada produto do literal é conferido campo a campo, pelo id, e a ordem
relativa entre eles é conferida. Produto que NÃO está no literal é validado em
`tests_produtos_novos.py`, contra as invariantes que o front exige de qualquer
produto. Ninguém sai sem guarda.
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
                "id": "base", "name": "ELITE BASE", "sigla": "BAS",
                "desc": "O plano da Fundação", "duration": "12 meses",
                "icon": "foundation", "price": 5997 * 12,
                "monthly": 5997, "recurring": True, "vigencia": 12,
            },
            {
                "id": "pre", "name": "ELITE PRÉ", "sigla": "EPR",
                "desc": "O plano do Primeiro Passo", "duration": "12 meses",
                "icon": "start", "price": 2997 * 12,
                "monthly": 2997, "recurring": True, "vigencia": 12,
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
            {
                "id": "conselho", "name": "ELITE CONSELHO", "sigla": "CON",
                "desc": "O plano do Conselho Consultivo", "duration": "12 meses",
                "icon": "groups", "price": 49997 * 12,
                "monthly": 49997, "recurring": True, "vigencia": 12,
            },
        ],
    },
    {
        # Travada e SEM produtos desde 17/08/2026: os três da semente nunca
        # abriram para venda e foram removidos. A categoria fica, para o
        # consultor ver que existe e ainda não abriu.
        "id": "treinamentos",
        "name": "Treinamentos",
        "icon": "school",
        "color": "gold",
        "desc": "Imersões e trilhas de desenvolvimento de líderes e equipes comerciais.",
        "locked": True,
        "products": [],
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
        # Mesma história dos Treinamentos: travada, e agora sem produtos.
        "id": "palestras",
        "name": "Palestras",
        "icon": "campaign",
        "color": "green",
        "desc": "Palestras in company e eventos de alto impacto.",
        "locked": True,
        "products": [],
    },
]


class ContratoDoCatalogoTest(TestCase):
    """O JSON servido tem que ser o mesmo que o front recebe hoje."""

    def setUp(self):
        self.client = APIClient()

    def _servido(self):
        resposta = self.client.get("/api/catalogo")
        self.assertEqual(resposta.status_code, 200)
        return resposta.json()["cats"]

    def test_get_publico_devolve_o_catalogo_de_producao(self):
        """Cada categoria e cada produto de produção, campo a campo."""
        cats = {c["id"]: c for c in self._servido()}

        for esperada in CATALOGO_EM_PRODUCAO:
            servida = cats.get(esperada["id"])
            self.assertIsNotNone(
                servida, f'A categoria "{esperada["id"]}" desapareceu do catálogo.'
            )

            # Campos da categoria (tudo menos a lista de produtos), incluindo a
            # AUSÊNCIA de chave: `locked` e `flow` só saem quando valem algo, e o
            # front testa `!!c.locked` / `c.flow === 'apn'`.
            self.assertEqual(
                {k: v for k, v in servida.items() if k != "products"},
                {k: v for k, v in esperada.items() if k != "products"},
                f'Campos da categoria "{esperada["id"]}" mudaram.',
            )

            produtos = {p["id"]: p for p in servida["products"]}
            for produto in esperada["products"]:
                self.assertIn(
                    produto["id"],
                    produtos,
                    f'O produto "{produto["id"]}" desapareceu de "{esperada["id"]}" — '
                    "e produto vendido que sai do catálogo deixa protocolo órfão.",
                )
                self.assertEqual(
                    produtos[produto["id"]],
                    produto,
                    f'O produto "{produto["id"]}" mudou em relação ao que está no ar.',
                )

    def test_ordem_relativa_dos_produtos_de_producao(self):
        """Produto novo pode entrar; reordenar o que já está no ar, não.

        O front lista `cat.products` na ordem em que vêm (`index.html`,
        `renderProduto`). Trocar a ordem dos planos Elite é mudar a tela do
        consultor sem ninguém ter pedido.
        """
        cats = {c["id"]: c for c in self._servido()}
        for esperada in CATALOGO_EM_PRODUCAO:
            servidos = [p["id"] for p in cats[esperada["id"]]["products"]]
            conhecidos = [p["id"] for p in esperada["products"]]
            self.assertEqual(
                [i for i in servidos if i in conhecidos],
                conhecidos,
                f'A ordem dos produtos de "{esperada["id"]}" mudou.',
            )

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
        """`recurring` ausente é como o front distingue avulso de recorrente.

        O avulso é criado aqui porque a semente não tem mais nenhum: os de
        Treinamentos e Palestras saíram em 17/08/2026. O contrato continua
        valendo, e é dele que este teste trata.
        """
        from .tests_escrita import cria_avulso

        cria_avulso()
        cats = self.client.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        avulso = next(p for p in elite["products"] if p["id"] == "intensivo")

        self.assertEqual(avulso["price"], 28000)
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
