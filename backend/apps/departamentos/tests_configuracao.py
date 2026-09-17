"""
A aba Departamentos do /admin: lista completa ou departamento fixo (TSK-585).

O que estes testes guardam, em ordem de dano:

1. O deploy não muda nada. Sem a diretoria salvar a aba, a rota pública responde
   a mesma lista, com a MESMA versão de antes — senão toda tela aberta acharia
   que a lista mudou.
2. Fixo inativo no Omie não trava a venda: volta a lista.
3. Só a diretoria grava, e cada gravação guarda quem foi.
"""
import hashlib
import json

from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel

from .models import ConfiguracaoDepartamento, Departamento
from .servicos import sincronizar
from .tests_departamentos import dep

ROTA = "/api/departamentos/configuracao"

LISTA = [
    dep("1", "1.Consultoria", "001.001"),
    dep("2", "2.Comercial", "001.009"),
    dep("3", "Copa", "001.017", ativo=False),
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
        return self.anonimo.get("/api/departamentos").json()

    def salva(self, corpo, usuario=None):
        return self.como(usuario or self.diretoria).put(ROTA, corpo, format="json")


class SemConfiguracaoTest(Base):
    def test_rota_publica_segue_com_a_lista_completa(self):
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual([d["codigo"] for d in corpo["departamentos"]], ["1", "2"])

    def test_versao_da_lista_e_a_mesma_de_antes_da_aba(self):
        """A conta de antes da TSK-585, copiada à mão. Se o hash da lista mudasse
        no deploy, todo lobby aberto mostraria "lista atualizada" sem nada ter
        mudado."""
        ativos = [["1", "1.Consultoria", "001.001"], ["2", "2.Comercial", "001.009"]]
        antes = hashlib.sha256(json.dumps(ativos, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        self.assertEqual(self.publico()["versao"], antes)

    def test_aba_abre_em_lista_sem_autor(self):
        corpo = self.como(self.diretoria).get(ROTA).json()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual(corpo["modo_em_vigor"], "lista")
        self.assertIsNone(corpo["fixo"])
        self.assertIsNone(corpo["alterado_em"])
        self.assertIsNone(corpo["alterado_por"])
        self.assertEqual([d["codigo"] for d in corpo["departamentos"]], ["1", "2"])
        self.assertIsNotNone(corpo["sincronizado_em"])


class FixoTest(Base):
    def test_fixo_e_o_unico_item_da_rota_publica(self):
        resposta = self.salva({"modo": "fixo", "codigo": "2"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Cache-Control"], "no-store")

        corpo = self.publico()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(
            corpo["departamentos"],
            [{"codigo": "2", "descricao": "2.Comercial", "estrutura": "001.009"}],
        )

    def test_gravacao_guarda_autor_e_devolve_o_estado_novo(self):
        corpo = self.salva({"modo": "fixo", "codigo": "1"}).json()

        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(corpo["modo_em_vigor"], "fixo")
        self.assertEqual(corpo["fixo"]["codigo"], "1")
        self.assertTrue(corpo["fixo"]["ativo"])
        self.assertEqual(corpo["alterado_por"], "diretoria@sejaap.com.br")
        self.assertIsNotNone(corpo["alterado_em"])
        self.assertEqual(ConfiguracaoDepartamento.objects.get().autor, self.diretoria)

    def test_cada_gravacao_e_uma_linha_e_vale_a_ultima(self):
        self.salva({"modo": "fixo", "codigo": "1"})
        self.salva({"modo": "fixo", "codigo": "2"})
        self.assertEqual([d["codigo"] for d in self.publico()["departamentos"]], ["2"])

        self.salva({"modo": "lista"})
        self.assertEqual(ConfiguracaoDepartamento.objects.count(), 3)
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual([d["codigo"] for d in corpo["departamentos"]], ["1", "2"])

    def test_lista_ignora_codigo_que_venha_junto(self):
        """A tela guarda o último fixo escolhido mesmo depois de voltar para a lista."""
        self.assertEqual(self.salva({"modo": "lista", "codigo": "1"}).status_code, 200)
        self.assertIsNone(ConfiguracaoDepartamento.objects.get().departamento)

    def test_versao_muda_ao_fixar_e_volta_ao_soltar(self):
        lista = self.publico()["versao"]
        self.salva({"modo": "fixo", "codigo": "1"})
        self.assertNotEqual(self.publico()["versao"], lista)

        self.salva({"modo": "lista"})
        self.assertEqual(self.publico()["versao"], lista)

    def test_fixo_no_unico_ativo_ainda_muda_a_versao(self):
        """Mesma lista, modo diferente: o lobby precisa saber que tem de travar."""
        sincronizar([dep("1", "1.Consultoria", "001.001")])
        lista = self.publico()
        self.salva({"modo": "fixo", "codigo": "1"})
        fixo = self.publico()

        self.assertEqual(fixo["departamentos"], lista["departamentos"])
        self.assertNotEqual(fixo["versao"], lista["versao"])


class FixoInativadoTest(Base):
    def setUp(self):
        super().setUp()
        self.salva({"modo": "fixo", "codigo": "2"})
        # O Omie inativa o 2.Comercial.
        sincronizar(
            [dep("1", "1.Consultoria", "001.001"), dep("2", "2.Comercial", "001.009", ativo=False)]
        )

    def test_consultor_volta_a_ver_a_lista(self):
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual([d["codigo"] for d in corpo["departamentos"]], ["1"])

    def test_aba_mostra_o_que_foi_salvo_e_o_que_vale(self):
        corpo = self.como(self.diretoria).get(ROTA).json()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(corpo["modo_em_vigor"], "lista")
        self.assertEqual(corpo["fixo"]["codigo"], "2")
        self.assertFalse(corpo["fixo"]["ativo"])

    def test_reativado_no_omie_o_fixo_volta_sozinho(self):
        sincronizar(LISTA)
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual([d["codigo"] for d in corpo["departamentos"]], ["2"])


class RecusaTest(Base):
    def assertRecusa(self, corpo, trecho):
        resposta = self.salva(corpo)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn(trecho, resposta.json()["erro"])
        self.assertFalse(ConfiguracaoDepartamento.objects.exists())

    def test_modo_desconhecido(self):
        self.assertRecusa({"modo": "aleatorio"}, "lista completa")

    def test_corpo_que_nao_e_objeto(self):
        self.assertRecusa(["fixo"], "inválida")

    def test_fixo_sem_codigo(self):
        self.assertRecusa({"modo": "fixo"}, "Escolha qual")

    def test_fixo_com_codigo_que_nao_existe(self):
        self.assertRecusa({"modo": "fixo", "codigo": "999"}, "não está na lista")

    def test_fixo_inativo(self):
        self.assertRecusa({"modo": "fixo", "codigo": "3"}, "inativo no Omie")


class PermissaoTest(Base):
    def test_anonimo_recebe_401_na_leitura_e_na_gravacao(self):
        self.assertEqual(self.anonimo.get(ROTA).status_code, 401)
        self.assertEqual(self.anonimo.put(ROTA, {"modo": "lista"}, format="json").status_code, 401)

    def test_gerente_recebe_403_e_nada_e_gravado(self):
        self.assertEqual(self.como(self.gerente).get(ROTA).status_code, 403)
        resposta = self.salva({"modo": "fixo", "codigo": "1"}, usuario=self.gerente)
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(ConfiguracaoDepartamento.objects.exists())

    def test_rota_publica_continua_sem_escrita(self):
        self.assertEqual(self.anonimo.put("/api/departamentos", {}, format="json").status_code, 405)


class RestricaoDoBancoTest(TestCase):
    def test_banco_recusa_fixo_sem_departamento(self):
        """A validação mora no serviço; o CHECK segura quem gravar por fora dele."""
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracaoDepartamento.objects.create(modo="fixo")
        self.assertFalse(Departamento.objects.exists())
