"""
Regra de cobrança por categoria (TSK-1059, ADR-002 §§13.3 e 13.5).

O que estes testes guardam, em ordem de dano:

1. O deploy não muda a data de venda nenhuma: a migration 0015 dá a cada
   categoria exatamente a regra geral de hoje, e o catálogo serve a mesma regra
   que servia.
2. Mudar a regra de uma categoria não mexe nas outras, e a exceção do produto
   continua vencendo a da categoria.
3. O /admin de hoje continua funcionando: o corpo antigo (`geral`) vale para
   todas as categorias.
4. Recusa não grava nada, e toda mudança tem autor na linha do tempo.
"""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.contas.models import Papel

from .models import Categoria, PoliticaCobranca, Vigencia
from .tests_escrita import cria_pessoa

GERAL = {"dia_vencimento": 15, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}
DIA_10 = {"dia_vencimento": 10, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 1}
CATEGORIAS = ["elite", "treinamentos", "apn", "produtos"]


class Base(TestCase):
    def setUp(self):
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.diretoria)

    def publica(self, corpo):
        return self.cliente.put("/api/cobranca", corpo, format="json")

    def catalogo(self):
        return APIClient().get("/api/catalogo").json()

    def regra_servida(self, slug):
        return next(c for c in self.catalogo()["cats"] if c["id"] == slug)["cobranca"]


class DiaDoDeployTest(Base):
    def test_toda_categoria_nasce_com_a_regra_geral(self):
        for slug in CATEGORIAS:
            with self.subTest(slug=slug):
                politica = PoliticaCobranca.objects.get(categoria__slug=slug)
                self.assertEqual(politica.como_dicionario(), GERAL)

    def test_o_catalogo_serve_a_regra_de_cada_categoria_igual_a_geral(self):
        corpo = self.catalogo()
        self.assertEqual(corpo["cobranca"], GERAL)
        for categoria in corpo["cats"]:
            self.assertEqual(categoria["cobranca"], GERAL, categoria["id"])

    def test_estado_da_cobranca_traz_cada_categoria(self):
        corpo = self.cliente.get("/api/cobranca").json()
        self.assertEqual(list(corpo["categorias"]), CATEGORIAS)
        self.assertEqual(corpo["categorias"]["elite"], {"nome": "Elite", **GERAL})
        self.assertFalse(corpo["categorias_divergem"])
        self.assertEqual(corpo["geral"], GERAL)

    def test_a_linha_do_tempo_de_cada_categoria_abre_com_a_data_da_geral(self):
        geral = Vigencia.objects.get(chave="geral", campo="dia_vencimento", vigente_ate__isnull=True)
        copia = Vigencia.objects.get(chave="categoria:elite", campo="dia_vencimento", vigente_ate__isnull=True)
        self.assertEqual(copia.vigente_de, geral.vigente_de)
        self.assertEqual(copia.valor, "15")
        self.assertIsNone(copia.autor)
        self.assertIn("copiada da regra geral", copia.rotulo)


class PorCategoriaTest(Base):
    def test_muda_so_a_categoria_mandada(self):
        resposta = self.publica({"categorias": {"elite": DIA_10}})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.regra_servida("elite"), DIA_10)
        self.assertEqual(self.regra_servida("apn"), GERAL)
        self.assertEqual(self.regra_servida("produtos"), GERAL)
        self.assertTrue(resposta.json()["categorias_divergem"])
        # A geral não é tocada pelo corpo novo: é a regra de categoria nova.
        self.assertEqual(PoliticaCobranca.geral_atual().como_dicionario(), GERAL)

    def test_linha_do_tempo_registra_so_o_que_mudou_com_autor(self):
        self.publica({"categorias": {"elite": {**GERAL, "dia_vencimento": 5}}})
        novas = Vigencia.objects.filter(chave="categoria:elite", autor=self.diretoria)
        self.assertEqual([(v.campo, v.valor) for v in novas], [("dia_vencimento", "5")])
        self.assertEqual(novas[0].rotulo, "Elite · cobrança")

    def test_excecoes_ausentes_no_corpo_novo_ficam_como_estao(self):
        excecao = {"categoria_id": "elite", "produto_id": "pro", **DIA_10}
        self.publica({"geral": GERAL, "excecoes": [excecao]})
        self.publica({"categorias": {"apn": DIA_10}})
        self.assertTrue(PoliticaCobranca.objects.filter(produto__slug="pro").exists())

    def test_excecoes_presentes_sao_a_lista_completa(self):
        excecao = {"categoria_id": "elite", "produto_id": "pro", **DIA_10}
        self.publica({"categorias": {}, "excecoes": [excecao]})
        self.assertTrue(PoliticaCobranca.objects.filter(produto__slug="pro").exists())
        self.publica({"categorias": {}, "excecoes": []})
        self.assertFalse(PoliticaCobranca.objects.filter(produto__slug="pro").exists())

    def test_excecao_do_produto_vence_a_da_categoria(self):
        excecao = {"categoria_id": "elite", "produto_id": "pro", "dia_vencimento": 3,
                   "primeiro_vencimento": "proximo", "entrada_prazo_dias": 0}
        self.publica({"categorias": {"elite": DIA_10}, "excecoes": [excecao]})
        elite = next(c for c in self.catalogo()["cats"] if c["id"] == "elite")
        self.assertEqual(elite["cobranca"], DIA_10)
        pro = next(p for p in elite["products"] if p["id"] == "pro")
        self.assertEqual(pro["cobranca"]["dia_vencimento"], 3)
        evo = next(p for p in elite["products"] if p["id"] == "evo")
        self.assertNotIn("cobranca", evo)

    def test_categoria_nova_comeca_pela_regra_geral(self):
        Categoria.objects.create(slug="mentorias", nome="Mentorias", ordem=99)
        nova = next(c for c in self.catalogo()["cats"] if c["id"] == "mentorias")
        self.assertEqual(nova["cobranca"], GERAL)
        estado = self.cliente.get("/api/cobranca").json()
        self.assertEqual(estado["categorias"]["mentorias"], {"nome": "Mentorias", **GERAL})
        # E dá para gravar a dela, que nasce na primeira gravação.
        self.publica({"categorias": {"mentorias": DIA_10}})
        self.assertEqual(self.regra_servida("mentorias"), DIA_10)


class CorpoAntigoTest(Base):
    def test_corpo_do_admin_antigo_vale_para_todas_as_categorias(self):
        self.publica({"categorias": {"elite": DIA_10}})
        resposta = self.publica({"geral": {**GERAL, "dia_vencimento": 20}, "excecoes": []})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        for slug in CATEGORIAS:
            self.assertEqual(self.regra_servida(slug)["dia_vencimento"], 20, slug)
        self.assertEqual(PoliticaCobranca.geral_atual().dia_vencimento, 20)
        self.assertFalse(resposta.json()["categorias_divergem"])

    def test_os_dois_corpos_juntos_sao_recusados(self):
        resposta = self.publica({"geral": GERAL, "categorias": {"elite": DIA_10}})
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(self.regra_servida("elite"), GERAL)


class RecusaTest(Base):
    def test_categoria_desconhecida_recusa_tudo(self):
        resposta = self.publica({"categorias": {"elite": DIA_10, "nao-existe": DIA_10}})
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("nao-existe", resposta.json()["erro"])
        self.assertEqual(self.regra_servida("elite"), GERAL)

    def test_valores_fora_dos_limites(self):
        for regra in (
            {**DIA_10, "dia_vencimento": 29},
            {**DIA_10, "dia_vencimento": 0},
            {**DIA_10, "primeiro_vencimento": "depois"},
            {**DIA_10, "entrada_prazo_dias": 91},
        ):
            with self.subTest(regra=regra):
                resposta = self.publica({"categorias": {"apn": regra}})
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("APN", resposta.json()["erro"])
        self.assertEqual(self.regra_servida("apn"), GERAL)

    def test_categorias_que_nao_e_objeto(self):
        self.assertEqual(self.publica({"categorias": ["elite"]}).status_code, 400)


class MigracaoDeDadoTest(TransactionTestCase):
    """A 0015 ida e volta, num banco migrado de verdade."""

    serialized_rollback = True
    antes = [("catalogo", "0014_politica_de_cobranca_por_categoria")]
    depois = [("catalogo", "0015_cada_categoria_com_a_regra_geral")]

    def migra(self, alvo):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(alvo)
        return executor.loader.project_state(alvo).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_ida_copia_a_geral_de_hoje_e_volta_devolve(self):
        apps = self.migra(self.antes)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        self.assertFalse(Politica.objects.filter(categoria__isnull=False).exists())
        # Uma geral diferente do padrão, para a cópia não passar por acaso.
        Politica.objects.filter(geral=True).update(
            dia_vencimento=7, primeiro_vencimento="proximo", entrada_prazo_dias=3
        )

        apps = self.migra(self.depois)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        Categoria = apps.get_model("catalogo", "Categoria")
        self.assertEqual(
            Politica.objects.filter(categoria__isnull=False).count(), Categoria.objects.count()
        )
        for politica in Politica.objects.filter(categoria__isnull=False):
            self.assertEqual(
                (politica.dia_vencimento, politica.primeiro_vencimento, politica.entrada_prazo_dias),
                (7, "proximo", 3),
            )

        # Volta com todas iguais (o /admin antigo mudou para dia 9): a geral
        # recebe o dia 9, e nenhuma venda muda de data ao desfazer.
        Politica.objects.filter(categoria__isnull=False).update(dia_vencimento=9)
        apps = self.migra(self.antes)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        Vigencia = apps.get_model("catalogo", "Vigencia")
        self.assertFalse(Politica.objects.filter(categoria__isnull=False).exists())
        self.assertEqual(Politica.objects.get(geral=True).dia_vencimento, 9)
        self.assertFalse(Vigencia.objects.filter(chave__startswith="categoria:").exists())
        self.assertEqual(
            Vigencia.objects.get(chave="geral", campo="dia_vencimento", vigente_ate__isnull=True).valor, "9"
        )

    def test_volta_com_categorias_diferentes_nao_inventa_uma_geral(self):
        self.migra(self.antes)
        apps = self.migra(self.depois)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        Politica.objects.filter(categoria__slug="elite").update(dia_vencimento=5)
        apps = self.migra(self.antes)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        self.assertEqual(Politica.objects.get(geral=True).dia_vencimento, 15)
