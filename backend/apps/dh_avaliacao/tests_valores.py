"""
A aba DH - Avaliação do /admin: valor de cada modalidade, livre ou fixo (TSK-1056).

O que estes testes guardam, em ordem de dano:

1. Sem a diretoria salvar nada, o lobby abre com os valores iniciais do pedido
   (Relatório R$ 150, Devolutiva R$ 450), os dois livres.
2. Fixar é aplicado pelo backend, na rota pública: é ela que o lobby lê, e é a
   mesma regra que o comprovante usa para recusar outro valor.
3. Só a diretoria grava, cada gravação é uma linha, e cada linha guarda quem foi.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel

from .models import ConfiguracaoValores

PUBLICA = "/api/dh-avaliacao/valores"
ROTA = "/api/dh-avaliacao/valores/configuracao"


def corpo(relatorio=150, relatorio_fixo=False, devolutiva=450, devolutiva_fixo=False):
    return {
        "modalidades": {
            "relatorio": {"valor": relatorio, "fixo": relatorio_fixo},
            "devolutiva": {"valor": devolutiva, "fixo": devolutiva_fixo},
        }
    }


class Base(TestCase):
    def setUp(self):
        self.anonimo = APIClient()
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)

    def como(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    def publico(self):
        resposta = self.anonimo.get(PUBLICA)
        self.assertEqual(resposta.status_code, 200)
        return resposta.json()

    def modalidades(self, dados=None):
        return {m["id"]: m for m in (dados or self.publico())["modalidades"]}

    def salva(self, dados, usuario=None):
        return self.como(usuario or self.diretoria).put(ROTA, dados, format="json")


class SemConfiguracaoTest(Base):
    def test_lobby_abre_com_os_valores_iniciais_livres(self):
        self.assertEqual(
            self.publico()["modalidades"],
            [
                {"id": "relatorio", "nome": "Relatório", "valor": 150, "fixo": False},
                {"id": "devolutiva", "nome": "Devolutiva", "valor": 450, "fixo": False},
            ],
        )

    def test_valores_sao_numeros_e_nao_texto(self):
        """O lobby soma isto. "150.00" em texto viraria concatenação."""
        for modalidade in self.publico()["modalidades"]:
            self.assertIsInstance(modalidade["valor"], int)

    def test_rota_publica_nao_e_cacheavel_nem_exige_login(self):
        resposta = self.anonimo.get(PUBLICA)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Cache-Control"], "no-store")

    def test_aba_abre_sem_autor(self):
        dados = self.como(self.diretoria).get(ROTA).json()
        self.assertIsNone(dados["alterado_em"])
        self.assertIsNone(dados["alterado_por"])
        self.assertEqual(dados["valores_iniciais"], {"relatorio": 150, "devolutiva": 450})
        self.assertFalse(any(m["fixo"] for m in dados["modalidades"]))


class GravacaoTest(Base):
    def test_fixar_a_devolutiva_trava_so_ela(self):
        resposta = self.salva(corpo(devolutiva=480, devolutiva_fixo=True))
        self.assertEqual(resposta.status_code, 200, resposta.json())
        self.assertEqual(resposta["Cache-Control"], "no-store")

        modalidades = self.modalidades()
        self.assertEqual(modalidades["devolutiva"]["valor"], 480)
        self.assertTrue(modalidades["devolutiva"]["fixo"])
        self.assertEqual(modalidades["relatorio"]["valor"], 150)
        self.assertFalse(modalidades["relatorio"]["fixo"])

    def test_gravacao_guarda_quem_e_quando(self):
        dados = self.salva(corpo(relatorio=175.5)).json()

        self.assertTrue(dados["ok"])
        self.assertEqual(dados["alterado_por"], "diretoria@sejaap.com.br")
        self.assertIsNotNone(dados["alterado_em"])
        self.assertEqual(self.modalidades(dados)["relatorio"]["valor"], 175.5)

        gravada = ConfiguracaoValores.objects.get()
        self.assertEqual(gravada.autor, self.diretoria)
        self.assertEqual(gravada.relatorio_valor, Decimal("175.50"))

    def test_cada_gravacao_e_uma_linha_e_vale_a_ultima(self):
        self.salva(corpo(devolutiva_fixo=True))
        self.salva(corpo(devolutiva=500))
        self.assertEqual(ConfiguracaoValores.objects.count(), 2)

        modalidades = self.modalidades()
        self.assertEqual(modalidades["devolutiva"]["valor"], 500)
        self.assertFalse(modalidades["devolutiva"]["fixo"])

    def test_valor_com_virgula_e_aceito(self):
        """A tela manda número, mas quem testa no terminal digita "150,90"."""
        self.assertEqual(self.salva(corpo(relatorio="150,90")).status_code, 200)
        self.assertEqual(ConfiguracaoValores.objects.get().relatorio_valor, Decimal("150.90"))

    def test_versao_muda_ao_fixar_e_volta_ao_soltar(self):
        """É pela versão que o lobby aberto percebe a trava sem recarregar."""
        livre = self.publico()["versao"]
        self.salva(corpo(devolutiva_fixo=True))
        self.assertNotEqual(self.publico()["versao"], livre)

        self.salva(corpo())
        self.assertEqual(self.publico()["versao"], livre)


class RecusaTest(Base):
    def assertRecusa(self, dados, trecho):
        resposta = self.salva(dados)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn(trecho, resposta.json()["erro"])
        self.assertFalse(ConfiguracaoValores.objects.exists())

    def test_corpo_que_nao_e_objeto(self):
        self.assertRecusa(["fixo"], "inválida")

    def test_sem_modalidades(self):
        self.assertRecusa({"relatorio": {"valor": 1}}, "inválida")

    def test_falta_uma_modalidade(self):
        dados = corpo()
        del dados["modalidades"]["devolutiva"]
        self.assertRecusa(dados, "Informe o valor da Devolutiva")

    def test_valor_zero(self):
        self.assertRecusa(corpo(relatorio=0), "maior que zero")

    def test_valor_negativo(self):
        self.assertRecusa(corpo(devolutiva=-10), "maior que zero")

    def test_valor_acima_do_teto(self):
        self.assertRecusa(corpo(relatorio=5_000_000), "no máximo")

    def test_valor_que_nao_e_numero(self):
        self.assertRecusa(corpo(relatorio="cento e cinquenta"), "inválido")

    def test_valor_ausente(self):
        self.assertRecusa(corpo(relatorio=None), "Informe o valor do Relatório")

    def test_booleano_no_lugar_do_valor(self):
        self.assertRecusa(corpo(relatorio=True), "Informe o valor do Relatório")

    def test_fixo_em_texto_nao_vira_verdadeiro(self):
        """`bool("false")` é True. Aceitar texto travaria o campo de todo consultor."""
        self.assertRecusa(corpo(devolutiva_fixo="false"), "fixo ou livre")


class PermissaoTest(Base):
    def test_anonimo_recebe_401_na_leitura_e_na_gravacao(self):
        self.assertEqual(self.anonimo.get(ROTA).status_code, 401)
        self.assertEqual(self.anonimo.put(ROTA, corpo(), format="json").status_code, 401)

    def test_gerente_recebe_403_e_nada_e_gravado(self):
        """Gerente autoriza UMA venda; isto vale para todas as próximas."""
        self.assertEqual(self.como(self.gerente).get(ROTA).status_code, 403)
        resposta = self.salva(corpo(devolutiva_fixo=True), usuario=self.gerente)
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(ConfiguracaoValores.objects.exists())

    def test_rota_publica_nao_aceita_escrita(self):
        self.assertEqual(self.anonimo.put(PUBLICA, corpo(), format="json").status_code, 405)


class RestricaoDoBancoTest(TestCase):
    def test_banco_recusa_valor_zero(self):
        """A validação mora no serviço; o CHECK segura quem gravar por fora dele."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfiguracaoValores.objects.create(
                relatorio_valor=Decimal("0"), devolutiva_valor=Decimal("450")
            )
