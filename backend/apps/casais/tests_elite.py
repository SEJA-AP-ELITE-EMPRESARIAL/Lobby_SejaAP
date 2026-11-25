"""
`POST /api/casais/elite` e a conferência por trás dela (TSK-1622).

- a resposta do Conecta vira o valor que a tela mostra: ativo → Elite
  (R$ 35.000,00); não ativo → cheio (R$ 39.997,00); sem resposta → cheio, com o
  motivo — nunca o Elite sem a conferência;
- o balde do Conecta é um só para o Lobby inteiro: a resposta fica guardada por
  CNPJ, uma falha pausa as perguntas de todos, e cada pessoa tem um teto das
  que vão de fato até lá;
- 400 para o que não é CNPJ (não chega ao Conecta), 401 sem sessão, só a sessão
  da tela de quem vende.

O Conecta aqui é um mock do `conecta.cliente_ativo`; o pedido HTTP de verdade é
o `tests_conecta`.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel
from apps.contas.para_testes import consultor, token_de
from apps.contas.tests_assercao import BaseAssercao

from . import servicos
from .conecta import SemResposta

ROTA = "/api/casais/elite"
ALVO = "apps.casais.servicos.conecta.cliente_ativo"
ATIVO = "22.122.190/0001-52"
INATIVO = "37.598.418/0001-52"
OUTRO = "63.580.153/0001-75"


def cliente_com_sessao(usuario):
    """A tela: o token de sessão de `usuario` no `Authorization`, de verdade."""
    return APIClient(HTTP_AUTHORIZATION=f"Bearer {token_de(usuario)}")


def conecta_falso(cnpj):
    """O Conecta: só o ATIVO é cliente ativo."""
    return cnpj == "22122190000152"


class Base(TestCase):
    def setUp(self):
        # A resposta guardada, a pausa e os tetos moram no cache: sem limpar, um
        # teste herdaria os do anterior.
        cache.clear()
        mock = patch(ALVO, side_effect=conecta_falso)
        self.conecta = mock.start()
        self.addCleanup(mock.stop)
        self.cliente = cliente_com_sessao(consultor())

    def pergunta(self, cnpj, cliente=None):
        return (cliente or self.cliente).post(ROTA, {"cnpj": cnpj}, format="json")


class RespostaTest(Base):
    def test_cliente_ativo_vale_o_elite(self):
        resposta = self.pergunta(ATIVO)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.json(), {
            "cliente_ativo": True,
            "conferido": True,
            "tabela": "elite",
            "valor": 35000,
            "motivo": None,
            "tentar_em": None,
            "mensagem": "Cliente ativo do Elite Empresarial no Conecta: vale o valor do "
                        "Elite, R$ 35.000,00.",
        })
        self.assertEqual(resposta["Cache-Control"], "no-store")
        # O CNPJ vai ao Conecta sem máscara.
        self.conecta.assert_called_once_with("22122190000152")

    def test_nao_ativo_vale_o_cheio(self):
        corpo = self.pergunta(INATIVO).json()
        self.assertEqual(
            (corpo["cliente_ativo"], corpo["conferido"], corpo["tabela"], corpo["valor"]),
            (False, True, "cheio", 39997),
        )
        self.assertIn("R$ 39.997,00", corpo["mensagem"])

    def test_conecta_sem_resposta_fica_no_cheio_e_diz_por_que(self):
        for motivo, trecho in (
            ("indisponivel", "Não foi possível conferir"),
            ("manutencao", "em manutenção"),
            ("limite", "consultas demais"),
        ):
            with self.subTest(motivo=motivo):
                cache.clear()
                self.conecta.side_effect = SemResposta(motivo)
                corpo = self.pergunta(OUTRO).json()
                self.assertEqual(
                    (corpo["cliente_ativo"], corpo["conferido"], corpo["tabela"], corpo["valor"]),
                    (None, False, "cheio", 39997),
                )
                self.assertEqual(corpo["motivo"], motivo)
                self.assertIn(trecho, corpo["mensagem"])
                self.assertIn("preço cheio, R$ 39.997,00", corpo["mensagem"])

    def test_defeito_inesperado_tambem_fica_no_cheio_e_pausa(self):
        """Um defeito que se repete depois do POST não vira uma pergunta por venda."""
        self.conecta.side_effect = RuntimeError("bug")
        with self.assertLogs("apps.casais.servicos", "ERROR"):
            corpo = self.pergunta(OUTRO).json()
        self.assertEqual((corpo["cliente_ativo"], corpo["tabela"]), (None, "cheio"))
        self.assertEqual(corpo["tentar_em"], servicos.PAUSA_DEPOIS_DE_FALHA)
        self.pergunta(ATIVO)
        self.conecta.assert_called_once()


class CnpjTest(Base):
    def test_o_que_nao_e_cnpj_nem_chega_ao_conecta(self):
        """A malformada também gasta o balde do Conecta."""
        for cnpj in ("", "123", "22.122.190/0001-53", "00.000.000/0000-00",
                     "775.927.214-65", None, 22122190000152, ["22122190000152"]):
            with self.subTest(cnpj=cnpj):
                resposta = self.pergunta(cnpj)
                self.assertEqual(resposta.status_code, 400)
                self.assertEqual(resposta.json(), {"erro": "CNPJ inválido."})
        self.assertEqual(APIClient().post(ROTA, "texto", content_type="text/plain").status_code, 401)
        self.assertEqual(self.cliente.post(ROTA, ["x"], format="json").status_code, 400)
        self.conecta.assert_not_called()

    def test_com_e_sem_mascara_sao_o_mesmo_cnpj(self):
        self.pergunta(ATIVO)
        self.assertTrue(self.pergunta("22122190000152").json()["cliente_ativo"])
        self.conecta.assert_called_once()

    def test_cnpj_alfanumerico_da_receita(self):
        """O exemplo da Receita (IN RFB 2.229/2024), em minúsculas e com máscara."""
        self.pergunta("12.abc.345/01de-35")
        self.conecta.assert_called_once_with("12ABC34501DE35")

    def test_normaliza_cpf(self):
        self.assertEqual(servicos.normaliza_cpf("775.927.214-65"), "77592721465")
        for cpf in ("775.927.214-66", "111.111.111-11", "7759272146", None, 77592721465):
            with self.subTest(cpf=cpf):
                self.assertIsNone(servicos.normaliza_cpf(cpf))


class BaldeDoConectaTest(Base):
    def test_a_resposta_fica_guardada_por_cnpj(self):
        for _ in range(3):
            self.assertTrue(self.pergunta(ATIVO).json()["cliente_ativo"])
        for _ in range(2):
            self.assertFalse(self.pergunta(INATIVO).json()["cliente_ativo"])
        self.assertEqual(self.conecta.call_count, 2)

    def test_outra_pessoa_le_a_resposta_guardada(self):
        self.pergunta(ATIVO)
        gerente = cliente_com_sessao(cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE))
        self.assertTrue(self.pergunta(ATIVO, cliente=gerente).json()["cliente_ativo"])
        self.conecta.assert_called_once()

    def test_o_cnpj_nao_fica_em_claro_no_cache(self):
        """Nem um hash dele sem segredo, que se desfaz por força bruta."""
        import hashlib

        self.pergunta(ATIVO)
        chave = servicos._chave_da_resposta("22122190000152")
        self.assertNotIn("22122190000152", chave)
        self.assertNotIn(hashlib.sha256(b"22122190000152").hexdigest()[:32], chave)
        self.assertEqual(cache.get(chave), "ativo")
        with override_settings(SECRET_KEY="outra-chave-de-teste-com-tamanho-suficiente-para-o-django"):
            self.assertNotEqual(servicos._chave_da_resposta("22122190000152"), chave)

    def test_sem_resposta_nao_fica_guardada_e_pausa_todo_mundo(self):
        self.conecta.side_effect = SemResposta("indisponivel")
        primeira = self.pergunta(OUTRO).json()
        self.assertEqual(primeira["tentar_em"], servicos.PAUSA_DEPOIS_DE_FALHA)

        # O Conecta voltou, mas a pausa vale para todos, e para qualquer CNPJ:
        # sem laço, e um Conecta pendurado não prende um worker por pergunta.
        self.conecta.side_effect = conecta_falso
        gerente = cliente_com_sessao(cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE))
        segunda = self.pergunta(ATIVO, cliente=gerente).json()
        self.assertEqual((segunda["cliente_ativo"], segunda["motivo"]), (None, "indisponivel"))
        self.assertLessEqual(segunda["tentar_em"], servicos.PAUSA_DEPOIS_DE_FALHA)
        self.assertEqual(self.conecta.call_count, 1)

        # Passada a pausa, pergunta de novo.
        cache.delete(servicos._CHAVE_DA_PAUSA)
        self.assertTrue(self.pergunta(ATIVO).json()["cliente_ativo"])
        self.assertEqual(self.conecta.call_count, 2)

    def test_o_retry_after_do_429_e_respeitado(self):
        self.conecta.side_effect = SemResposta("limite", 437)
        self.assertEqual(self.pergunta(OUTRO).json()["tentar_em"], 437)
        self.conecta.side_effect = conecta_falso
        corpo = self.pergunta(ATIVO).json()
        self.assertEqual((corpo["cliente_ativo"], corpo["motivo"]), (None, "limite"))
        self.assertGreater(corpo["tentar_em"], 430)
        self.assertEqual(self.conecta.call_count, 1)

    def test_retry_after_absurdo_vale_ate_uma_hora(self):
        self.conecta.side_effect = SemResposta("limite", 999999)
        self.assertEqual(self.pergunta(OUTRO).json()["tentar_em"], servicos.PAUSA_MAXIMA)

    def test_conecta_sem_configuracao_nao_pausa(self):
        self.conecta.side_effect = SemResposta("nao_configurado")
        self.assertIsNone(self.pergunta(OUTRO).json()["tentar_em"])
        self.assertIsNone(cache.get(servicos._CHAVE_DA_PAUSA))

    @override_settings(LOBBY_CASAIS_CONSULTAS_POR_HORA=2)
    def test_teto_de_perguntas_ao_conecta_por_pessoa(self):
        self.pergunta(ATIVO)
        self.pergunta(INATIVO)
        # A resposta guardada não gasta o teto.
        self.assertTrue(self.pergunta(ATIVO).json()["cliente_ativo"])
        corpo = self.pergunta(OUTRO).json()
        self.assertEqual((corpo["cliente_ativo"], corpo["motivo"]), (None, "limite_da_pessoa"))
        self.assertIn("muitas consultas", corpo["mensagem"])
        self.assertGreater(corpo["tentar_em"], 0)
        self.assertLessEqual(corpo["tentar_em"], 3601)
        self.assertEqual(self.conecta.call_count, 2)

        # O teto é da pessoa: quem esgotou o seu não fecha a pergunta dos outros.
        gerente = cliente_com_sessao(cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE))
        self.assertFalse(self.pergunta(OUTRO, cliente=gerente).json()["cliente_ativo"])
        self.assertEqual(self.conecta.call_count, 3)

    def test_cache_fora_nao_derruba_a_pergunta(self):
        with patch("apps.casais.servicos.cache") as quebrado, \
                self.assertLogs("apps.casais.servicos", "WARNING"):
            quebrado.get.side_effect = ConnectionError("redis")
            quebrado.set.side_effect = ConnectionError("redis")
            quebrado.add.side_effect = ConnectionError("redis")
            corpo = self.pergunta(ATIVO).json()
        self.assertTrue(corpo["cliente_ativo"])


class UmaPerguntaPorCnpjTest(Base):
    """Um CNPJ é perguntado por uma requisição de cada vez; as outras esperam a resposta."""

    CNPJ = "22122190000152"

    def test_duas_ao_mesmo_tempo_fazem_uma_pergunta(self):
        import threading
        import time

        def devagar(cnpj):
            time.sleep(0.4)
            return conecta_falso(cnpj)

        self.conecta.side_effect = devagar
        respostas = []
        threads = [threading.Thread(target=lambda: respostas.append(servicos.conferir_cnpj(self.CNPJ)))
                   for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([r.cliente_ativo for r in respostas], [True, True, True])
        self.conecta.assert_called_once()

    def test_quem_chega_depois_le_a_resposta_da_outra(self):
        import threading

        chave = servicos._chave_da_resposta(self.CNPJ)
        cache.add(chave + ":perguntando", 1, 10)

        def a_outra_responde():
            cache.set(chave, "inativo", 60)
            cache.delete(chave + ":perguntando")

        threading.Timer(0.3, a_outra_responde).start()
        self.assertIs(servicos.conferir_cnpj(self.CNPJ).cliente_ativo, False)
        self.conecta.assert_not_called()

    def test_a_outra_terminou_sem_resposta(self):
        """A outra levou 429: esta sai com a pausa que ela deixou, sem perguntar."""
        import threading
        import time

        chave = servicos._chave_da_resposta(self.CNPJ)
        cache.add(chave + ":perguntando", 1, 10)

        def a_outra_falha():
            cache.set(servicos._CHAVE_DA_PAUSA, {"ate": time.time() + 30, "motivo": "limite"}, 30)
            cache.delete(chave + ":perguntando")

        threading.Timer(0.3, a_outra_falha).start()
        conferencia = servicos.conferir_cnpj(self.CNPJ)
        self.assertEqual((conferencia.cliente_ativo, conferencia.motivo), (None, "limite"))
        self.assertGreater(conferencia.tentar_em, 25)
        self.conecta.assert_not_called()

    def test_a_outra_pendurada_nao_prende_esta_para_sempre(self):
        chave = servicos._chave_da_resposta(self.CNPJ)
        cache.add(chave + ":perguntando", 1, 10)
        with patch.object(servicos, "ESPERA_MAXIMA", 0.5), patch.object(servicos, "ESPERA_INTERVALO", 0.05):
            conferencia = servicos.conferir_cnpj(self.CNPJ)
        self.assertEqual((conferencia.cliente_ativo, conferencia.motivo), (None, "indisponivel"))
        self.conecta.assert_not_called()

    def test_quem_pega_a_trava_depois_de_a_outra_responder_nao_pergunta_de_novo(self):
        """A outra perguntou e soltou a trava entre a 1ª leitura desta e a trava."""
        chave = servicos._chave_da_resposta(self.CNPJ)
        pega = servicos._pega_a_trava

        def a_outra_respondeu_no_meio(trava):
            cache.set(chave, "ativo", 60)
            return pega(trava)

        with patch.object(servicos, "_pega_a_trava", side_effect=a_outra_respondeu_no_meio):
            self.assertIs(servicos.conferir_cnpj(self.CNPJ).cliente_ativo, True)
        self.conecta.assert_not_called()

        cache.clear()

        def a_outra_falhou_no_meio(trava):
            cache.set(servicos._CHAVE_DA_PAUSA, {"ate": __import__("time").time() + 30, "motivo": "limite"}, 30)
            return pega(trava)

        with patch.object(servicos, "_pega_a_trava", side_effect=a_outra_falhou_no_meio):
            self.assertEqual(servicos.conferir_cnpj(self.CNPJ).motivo, "limite")
        self.conecta.assert_not_called()

    def test_a_trava_e_solta_depois_da_pergunta(self):
        servicos.conferir_cnpj(self.CNPJ)
        self.assertIsNone(cache.get(servicos._chave_da_resposta(self.CNPJ) + ":perguntando"))
        self.conecta.side_effect = SemResposta("indisponivel")
        cache.clear()
        servicos.conferir_cnpj(self.CNPJ)
        self.assertIsNone(cache.get(servicos._chave_da_resposta(self.CNPJ) + ":perguntando"))


class SessaoTest(Base):
    def test_sem_sessao_401_e_nao_pergunta(self):
        resposta = APIClient().post(ROTA, {"cnpj": ATIVO}, format="json")
        self.assertEqual(resposta.status_code, 401)
        self.conecta.assert_not_called()

    def test_o_segredo_do_n8n_nao_e_sessao(self):
        with override_settings(LOBBY_N8N_TOKEN="segredo-do-n8n"):
            resposta = self.pergunta(ATIVO, cliente=APIClient(HTTP_AUTHORIZATION="Bearer segredo-do-n8n"))
        self.assertEqual(resposta.status_code, 401)
        self.conecta.assert_not_called()

    def test_so_post(self):
        for metodo in ("get", "put", "patch", "delete"):
            with self.subTest(metodo=metodo):
                self.assertEqual(getattr(self.cliente, metodo)(ROTA).status_code, 405)

    def test_teto_da_rota_por_pessoa(self):
        for _ in range(60):
            self.assertEqual(self.pergunta(ATIVO).status_code, 200)
        self.assertEqual(self.pergunta(ATIVO).status_code, 429)
        gerente = cliente_com_sessao(cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE))
        self.assertEqual(self.pergunta(ATIVO, cliente=gerente).status_code, 200)


class AssercaoDoConectaTest(BaseAssercao):
    """O Admin Lobby do Conecta não gasta o balde da pergunta: 403 antes da view."""

    def test_assercao_e_recusada(self):
        with patch(ALVO) as conecta:
            resposta = self.chama("post", ROTA, self.par.assina(), corpo={"cnpj": ATIVO})
        self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")
        conecta.assert_not_called()
