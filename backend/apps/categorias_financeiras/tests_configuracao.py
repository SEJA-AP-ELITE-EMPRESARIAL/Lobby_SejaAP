"""
A aba Categorias do /admin: lista completa ou categoria fixa (TSK-877).

As mesmas guardas da aba Departamentos: sem gravação vale a lista; fixa inativa
não trava a venda; só a diretoria grava, e cada gravação guarda quem foi.
"""
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel

from .models import ConfiguracaoCategoriaFinanceira
from .servicos import sincronizar
from .tests_categorias import cat

ROTA = "/api/categorias-financeiras/configuracao"

LISTA = [
    cat("1.01", "Consultoria"),
    cat("1.02", "Eventos"),
    cat("1.03", "Patrocínio", ativo=False),
]


class Base(TestCase):
    def setUp(self):
        sincronizar(LISTA)
        self.anonimo = APIClient()
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)

    def como(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    def publico(self):
        return self.anonimo.get("/api/categorias-financeiras").json()

    def salva(self, corpo, usuario=None):
        return self.como(usuario or self.diretoria).put(ROTA, corpo, format="json")


class SemConfiguracaoTest(Base):
    def test_rota_publica_segue_com_a_lista_completa(self):
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual([c["codigo"] for c in corpo["categorias"]], ["1.01", "1.02"])

    def test_aba_abre_em_lista_sem_autor(self):
        corpo = self.como(self.diretoria).get(ROTA).json()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual(corpo["modo_em_vigor"], "lista")
        self.assertIsNone(corpo["fixa"])
        self.assertIsNone(corpo["alterado_em"])
        self.assertIsNone(corpo["alterado_por"])
        self.assertEqual([c["codigo"] for c in corpo["categorias"]], ["1.01", "1.02"])
        self.assertIsNotNone(corpo["sincronizado_em"])


class NuncaSincronizadoTest(TestCase):
    def test_aba_abre_vazia_e_diz_que_nunca_sincronizou(self):
        diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        cliente = APIClient()
        cliente.force_authenticate(user=diretoria)
        corpo = cliente.get(ROTA).json()
        self.assertEqual(corpo["categorias"], [])
        self.assertIsNone(corpo["sincronizado_em"])


class FixaTest(Base):
    def test_fixa_e_o_unico_item_da_rota_publica(self):
        resposta = self.salva({"modo": "fixo", "codigo": "1.02"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Cache-Control"], "no-store")

        corpo = self.publico()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(corpo["categorias"], [{"codigo": "1.02", "descricao": "Eventos"}])

    def test_gravacao_guarda_autor_e_devolve_o_estado_novo(self):
        corpo = self.salva({"modo": "fixo", "codigo": "1.01"}).json()

        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(corpo["modo_em_vigor"], "fixo")
        self.assertEqual(corpo["fixa"]["codigo"], "1.01")
        self.assertTrue(corpo["fixa"]["ativo"])
        self.assertEqual(corpo["alterado_por"], "diretoria@sejaap.com.br")
        self.assertIsNotNone(corpo["alterado_em"])
        self.assertEqual(ConfiguracaoCategoriaFinanceira.objects.get().autor, self.diretoria)

    def test_cada_gravacao_e_uma_linha_e_vale_a_ultima(self):
        self.salva({"modo": "fixo", "codigo": "1.01"})
        self.salva({"modo": "fixo", "codigo": "1.02"})
        self.assertEqual([c["codigo"] for c in self.publico()["categorias"]], ["1.02"])

        self.salva({"modo": "lista"})
        self.assertEqual(ConfiguracaoCategoriaFinanceira.objects.count(), 3)
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual([c["codigo"] for c in corpo["categorias"]], ["1.01", "1.02"])

    def test_lista_ignora_codigo_que_venha_junto(self):
        """A tela guarda a última fixa escolhida mesmo depois de voltar para a lista."""
        self.assertEqual(self.salva({"modo": "lista", "codigo": "1.01"}).status_code, 200)
        self.assertIsNone(ConfiguracaoCategoriaFinanceira.objects.get().categoria)

    def test_versao_muda_ao_fixar_e_volta_ao_soltar(self):
        lista = self.publico()["versao"]
        self.salva({"modo": "fixo", "codigo": "1.01"})
        self.assertNotEqual(self.publico()["versao"], lista)

        self.salva({"modo": "lista"})
        self.assertEqual(self.publico()["versao"], lista)

    def test_fixa_na_unica_ativa_ainda_muda_a_versao(self):
        sincronizar([cat("1.01", "Consultoria")])
        lista = self.publico()
        self.salva({"modo": "fixo", "codigo": "1.01"})
        fixa = self.publico()

        self.assertEqual(fixa["categorias"], lista["categorias"])
        self.assertNotEqual(fixa["versao"], lista["versao"])


class FixaInativadaTest(Base):
    def setUp(self):
        super().setUp()
        self.salva({"modo": "fixo", "codigo": "1.02"})
        sincronizar([cat("1.01", "Consultoria"), cat("1.02", "Eventos", ativo=False)])

    def test_consultor_volta_a_ver_a_lista(self):
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual([c["codigo"] for c in corpo["categorias"]], ["1.01"])

    def test_aba_mostra_o_que_foi_salvo_e_o_que_vale(self):
        corpo = self.como(self.diretoria).get(ROTA).json()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(corpo["modo_em_vigor"], "lista")
        self.assertEqual(corpo["fixa"]["codigo"], "1.02")
        self.assertFalse(corpo["fixa"]["ativo"])

    def test_reativada_na_fonte_a_fixa_volta_sozinha(self):
        sincronizar(LISTA)
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual([c["codigo"] for c in corpo["categorias"]], ["1.02"])


class RecusaTest(Base):
    def assertRecusa(self, corpo, trecho):
        resposta = self.salva(corpo)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn(trecho, resposta.json()["erro"])
        self.assertFalse(ConfiguracaoCategoriaFinanceira.objects.exists())

    def test_modo_desconhecido(self):
        self.assertRecusa({"modo": "aleatorio"}, "lista completa")

    def test_corpo_que_nao_e_objeto(self):
        self.assertRecusa(["fixo"], "inválida")

    def test_fixa_sem_codigo(self):
        self.assertRecusa({"modo": "fixo"}, "Escolha qual")

    def test_fixa_com_codigo_que_nao_existe(self):
        self.assertRecusa({"modo": "fixo", "codigo": "9.99"}, "não está na lista")

    def test_fixa_inativa(self):
        self.assertRecusa({"modo": "fixo", "codigo": "1.03"}, "inativa")


class PermissaoTest(Base):
    def test_anonimo_recebe_401_na_leitura_e_na_gravacao(self):
        self.assertEqual(self.anonimo.get(ROTA).status_code, 401)
        self.assertEqual(self.anonimo.put(ROTA, {"modo": "lista"}, format="json").status_code, 401)

    def test_gerente_recebe_403_e_nada_e_gravado(self):
        self.assertEqual(self.como(self.gerente).get(ROTA).status_code, 403)
        resposta = self.salva({"modo": "fixo", "codigo": "1.01"}, usuario=self.gerente)
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(ConfiguracaoCategoriaFinanceira.objects.exists())

    def test_rota_publica_continua_sem_escrita(self):
        self.assertEqual(self.anonimo.put("/api/categorias-financeiras", {}, format="json").status_code, 405)


class RestricaoDoBancoTest(TestCase):
    def test_banco_recusa_fixa_sem_categoria(self):
        """A validação mora no serviço; o CHECK segura quem gravar por fora dele."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracaoCategoriaFinanceira.objects.create(modo="fixo")
        self.assertFalse(ConfiguracaoCategoriaFinanceira.objects.exists())
