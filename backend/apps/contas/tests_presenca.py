"""
O sinal de presença da tela (`POST /api/presenca`, TSK-1520).

Regras do contrato, uma por bloco:

- a rota repassa ao Conecta, pelo `enviar_presenca` do cliente 1.6.0, quem está
  logado, o módulo, a tela e a última interação, e responde 204 — inclusive com
  o Conecta fora, em manutenção ou recusando;
- 400 para o corpo que não é um sinal; 401 sem a sessão do lobby;
- só a sessão da TELA conta: a asserção do Conecta, o segredo do n8n, o token
  do Prometheus, a sessão do /django-admin/ e o token de quem autoriza a
  negociação nunca chegam ao `enviar_presenca`;
- teto por pessoa (20/min);
- nada de presença fica no banco do Lobby: o caminho inteiro não faz INSERT,
  UPDATE nem DELETE.

A primeira metade troca o `enviar_presenca` por um mock; a segunda passa pelo
cliente de verdade, contra o Conecta falso do loopback (`tests_revalidacao`).
"""
import http.server
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from django.apps import apps
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

import identidade_client

from apps.catalogo.tests_escrita import cria_pessoa

from .models import Papel
from .para_testes import consultor, token_de
from .tests_assercao import BaseAssercao
from .tests_revalidacao import CHAVE, ConectaFalsoTest, _ConectaFalso

ROTA = "/api/presenca"
ALVO = "apps.contas.presenca.identidade_client.enviar_presenca"
SINAL = {
    "modulo": "Elite",
    "tela": "Pagamento · ELITE PRO",
    "ultima_interacao": "2026-11-17T13:02:11.000Z",
}
ESCRITA = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def cliente_com_sessao(usuario):
    """O lobby: o token de sessão de `usuario` no `Authorization`, de verdade."""
    return APIClient(HTTP_AUTHORIZATION=f"Bearer {token_de(usuario)}")


class BasePresenca(TestCase):
    def setUp(self):
        # O teto por pessoa mora no cache: sem limpar, um teste herdaria o balde.
        cache.clear()
        self.carla = consultor("carla@sejaap.com.br")
        self.cliente = cliente_com_sessao(self.carla)
        repasse = patch(ALVO, return_value=True)
        self.enviar = repasse.start()
        self.addCleanup(repasse.stop)

    def sinal(self, corpo=SINAL, cliente=None, **extra):
        return (cliente or self.cliente).post(ROTA, corpo, format="json", **extra)


class RepasseTest(BasePresenca):
    def test_repassa_quem_esta_logado_o_modulo_a_tela_e_a_ultima_interacao(self):
        resposta = self.sinal()

        self.assertEqual(resposta.status_code, 204, resposta.content)
        self.assertEqual(resposta.content, b"")
        self.enviar.assert_called_once_with(
            self.carla,
            modulo="Elite",
            tela="Pagamento · ELITE PRO",
            ultima_interacao=datetime(2026, 11, 17, 13, 2, 11, tzinfo=timezone.utc),
        )

    def test_a_ultima_interacao_com_outro_fuso_chega_no_mesmo_instante(self):
        self.sinal({**SINAL, "ultima_interacao": "2026-11-17T10:02:11-03:00"})
        quando = self.enviar.call_args.kwargs["ultima_interacao"]
        self.assertEqual(quando, datetime(2026, 11, 17, 13, 2, 11, tzinfo=timezone.utc))
        self.assertIsNotNone(quando.tzinfo)

    def test_so_o_modulo_basta(self):
        """A tela e a última interação são opcionais (a tela sempre manda as duas)."""
        self.assertEqual(self.sinal({"modulo": "Lobby"}).status_code, 204)
        self.enviar.assert_called_once_with(
            self.carla, modulo="Lobby", tela="", ultima_interacao=None
        )

    def test_ultima_interacao_nula_vai_como_nenhuma(self):
        self.assertEqual(self.sinal({**SINAL, "ultima_interacao": None}).status_code, 204)
        self.assertIsNone(self.enviar.call_args.kwargs["ultima_interacao"])

    def test_cada_pessoa_e_ela_mesma(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        self.sinal(cliente=cliente_com_sessao(gerente))
        self.assertEqual(self.enviar.call_args.args, (gerente,))

    def test_os_tamanhos_do_conecta_cabem(self):
        corpo = {"modulo": "M" * 60, "tela": "T" * 120, "ultima_interacao": SINAL["ultima_interacao"]}
        self.assertEqual(self.sinal(corpo).status_code, 204)
        self.assertEqual(self.enviar.call_args.kwargs["tela"], "T" * 120)

    def test_204_quando_o_conecta_nao_gravou(self):
        """`enviar_presenca` devolve False com o Conecta fora: a tela não tem o que fazer com isso."""
        self.enviar.return_value = False
        self.assertEqual(self.sinal().status_code, 204)


class CorpoInvalidoTest(BasePresenca):
    def test_400_sem_chegar_ao_conecta(self):
        casos = {
            "sem módulo": {"tela": "Pagamento"},
            "módulo vazio": {"modulo": "   "},
            "módulo de 61": {"modulo": "M" * 61},
            "tela de 121": {"modulo": "Elite", "tela": "T" * 121},
            "módulo que não é texto": {"modulo": ["Elite"]},
            "tela que não é texto": {"modulo": "Elite", "tela": {"a": 1}},
            "caractere nulo": {"modulo": "Eli\x00te"},
            "interação que não é data": {"modulo": "Elite", "ultima_interacao": "agora há pouco"},
            "interação sem fuso": {"modulo": "Elite", "ultima_interacao": "2026-11-17T13:02:11"},
            "interação impossível": {"modulo": "Elite", "ultima_interacao": "2026-13-40T13:02:11Z"},
            "lista no lugar do objeto": [SINAL],
            "corpo vazio": {},
        }
        for nome, corpo in casos.items():
            with self.subTest(nome):
                resposta = self.sinal(corpo)
                self.assertEqual(resposta.status_code, 400, resposta.content)
                self.assertTrue(resposta.json()["erro"])
        self.enviar.assert_not_called()

    def test_json_quebrado_e_400(self):
        resposta = self.cliente.post(ROTA, data="{modulo:", content_type="application/json")
        self.assertEqual(resposta.status_code, 400)
        self.enviar.assert_not_called()


class SoASessaoDaTelaTest(BasePresenca):
    """Sem a sessão do lobby, 401, e nada vai ao Conecta."""

    def assertRecusada(self, resposta, status=401):
        self.assertEqual(resposta.status_code, status, resposta.content)
        self.enviar.assert_not_called()

    def test_sem_autenticacao(self):
        self.assertRecusada(self.sinal(cliente=APIClient()))

    def test_token_vencido(self):
        vencido = AccessToken.for_user(self.carla)
        vencido.set_exp(lifetime=-timedelta(minutes=1))
        self.assertRecusada(self.sinal(cliente=APIClient(HTTP_AUTHORIZATION=f"Bearer {vencido}")))

    def test_token_assinado_por_outra_chave(self):
        """O mesmo conteúdo de um token do Lobby, assinado por quem não tem a chave daqui."""
        conteudo = AccessToken.for_user(self.carla).payload
        de_fora = jwt.encode(conteudo, "outra-chave-que-nao-e-a-do-lobby", algorithm="HS256")
        self.assertRecusada(self.sinal(cliente=APIClient(HTTP_AUTHORIZATION=f"Bearer {de_fora}")))

    def test_o_segredo_do_n8n_nao_e_sessao(self):
        with override_settings(LOBBY_N8N_TOKEN="segredo-do-n8n"):
            self.assertRecusada(
                self.sinal(cliente=APIClient(HTTP_AUTHORIZATION="Bearer segredo-do-n8n"))
            )

    def test_o_token_do_prometheus_nao_e_sessao(self):
        with override_settings(LOBBY_METRICS_TOKEN="token-do-prometheus"):
            self.assertRecusada(
                self.sinal(cliente=APIClient(HTTP_AUTHORIZATION="Bearer token-do-prometheus"))
            )

    def test_a_sessao_do_django_admin_nao_conta(self):
        """Quem está no /django-admin/ não está na tela do lobby."""
        diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        diretoria.is_staff = True
        diretoria.save()
        cliente = APIClient()
        cliente.force_login(diretoria)
        self.assertRecusada(self.sinal(cliente=cliente))

    def test_o_cabecalho_de_quem_autoriza_a_negociacao_nao_e_lido(self):
        """O `X-Lobby-Autorizacao` só existe no comprovante; aqui não é lido.

        O token dele é um JWT de sessão como o do login (o modal do gerente
        entra pelo mesmo `POST /api/sessao`): o que segura é a tela mandá-lo só
        nesse cabeçalho, e nunca no `Authorization`.
        """
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        cliente = APIClient(HTTP_X_LOBBY_AUTORIZACAO=f"Bearer {token_de(gerente)}")
        self.assertRecusada(self.sinal(cliente=cliente))

    def test_quem_o_conecta_desativou_nao_manda_sinal(self):
        with override_settings(AUTH_CENTRAL_ATIVO=True, IDENTIDADE_APP_KEY=CHAVE), \
                patch("identidade_client.ClienteIdentidade") as Cliente:
            Cliente.return_value.obter.return_value = {"ativo": False}
            self.assertRecusada(self.sinal())

    def test_so_post(self):
        for metodo in ("get", "put", "patch", "delete"):
            with self.subTest(metodo=metodo):
                self.assertEqual(getattr(self.cliente, metodo)(ROTA).status_code, 405)
        self.enviar.assert_not_called()


class AssercaoDoConectaTest(BaseAssercao):
    """A asserção do Conecta (a integração do Admin Lobby) não é a tela de ninguém.

    Com a integração LIGADA e uma asserção boa, de uma pessoa que tem conta aqui,
    a rota responde o 403 `ROTA_NAO_LIBERADA` e o `enviar_presenca` nunca roda:
    o Admin Lobby aberto no Conecta não faz a diretoria aparecer no Lobby.
    """

    def setUp(self):
        super().setUp()
        repasse = patch(ALVO, return_value=True)
        self.enviar = repasse.start()
        self.addCleanup(repasse.stop)

    def test_assercao_boa_e_recusada(self):
        # Contraprova: a mesma asserção abre a rota da integração.
        self.assertEqual(self.get().status_code, 200)
        resposta = self.chama("post", ROTA, self.par.assina(), corpo=SINAL)
        self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")
        self.enviar.assert_not_called()

    def test_assercao_junto_da_sessao_tambem(self):
        """Quem manda o cabeçalho da asserção é a integração, mesmo com um token de sessão ao lado."""
        resposta = APIClient().post(
            ROTA, SINAL, format="json",
            HTTP_X_CONECTA_ASSERCAO=self.par.assina(),
            HTTP_AUTHORIZATION=f"Bearer {token_de(self.diretoria)}",
        )
        self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")
        self.enviar.assert_not_called()

    def test_a_rota_nao_esta_na_lista_da_integracao(self):
        from .autenticacao_conecta import ROTAS_LIBERADAS

        self.assertNotIn("presenca", ROTAS_LIBERADAS)


class TetoPorPessoaTest(BasePresenca):
    def test_vinte_por_minuto_por_pessoa(self):
        for _ in range(20):
            self.assertEqual(self.sinal().status_code, 204)
        resposta = self.sinal()
        self.assertEqual(resposta.status_code, 429)
        self.assertIn("erro", resposta.json())
        self.assertEqual(self.enviar.call_count, 20)

        # O balde é da pessoa: outra, no mesmo aparelho, segue.
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        self.assertEqual(self.sinal(cliente=cliente_com_sessao(gerente)).status_code, 204)


class SemHistoricoTest(TestCase):
    """Nada de presença fica no Lobby (decisão de 16/11/2026)."""

    def test_nenhum_model_de_presenca(self):
        nomes = [m._meta.label_lower for m in apps.get_models()]
        self.assertFalse([n for n in nomes if "presen" in n], nomes)


# ----- com o cliente de verdade, contra o Conecta falso ------------------------

class PresencaContraOConectaTest(ConectaFalsoTest):
    def setUp(self):
        super().setUp()
        # A pausa de 60 s depois de uma falha é do PROCESSO (global do cliente):
        # sem zerar, o teste que derruba o Conecta calaria os seguintes.
        pausa = patch("identidade_client._PRESENCA_PAUSADA_ATE", 0.0)
        pausa.start()
        self.addCleanup(pausa.stop)
        self.carla = consultor("carla@sejaap.com.br")
        self.identidade_id = str(self.carla.vinculo_identidade.identidade_id)
        self.cliente = cliente_com_sessao(self.carla)
        # A revalidação da sessão (cliente 1.5) pergunta se a pessoa vale.
        self.responde("GET", f"/api/v1/identidades/{self.identidade_id}", 200, {"ativo": True})

    def sinal(self):
        return self.cliente.post(ROTA, SINAL, format="json")

    def sinais_recebidos(self):
        return [r for r in _ConectaFalso.recebidas if r["caminho"] == "/api/v1/presenca"]

    def test_o_sinal_chega_ao_conecta_com_a_chave_do_lobby(self):
        self.responde("POST", "/api/v1/presenca", 204, {})
        self.assertEqual(self.sinal().status_code, 204)

        [recebido] = self.sinais_recebidos()
        self.assertEqual(recebido["metodo"], "POST")
        self.assertEqual(recebido["cabecalhos"]["Authorization"], CHAVE)
        self.assertEqual(recebido["corpo"], {
            "identidade_id": self.identidade_id,
            "modulo": "Elite",
            "tela": "Pagamento · ELITE PRO",
            "ultima_interacao": "2026-11-17T13:02:11+00:00",
        })

    def test_204_com_o_conecta_respondendo_erro(self):
        for codigo, corpo in ((503, {"erro": "manutencao"}), (500, {"erro": "?"}),
                              (403, {"erro": "app_nao_autorizado"}), (429, {"erro": "limite"})):
            with self.subTest(codigo=codigo), \
                    patch("identidade_client._PRESENCA_PAUSADA_ATE", 0.0), \
                    self.assertLogs("identidade_client", level="WARNING"):
                self.responde("POST", "/api/v1/presenca", codigo, corpo)
                self.assertEqual(self.sinal().status_code, 204)

    def test_204_com_o_conecta_fora_do_ar(self):
        livre = http.server.HTTPServer(("127.0.0.1", 0), _ConectaFalso)
        porta = livre.server_address[1]
        livre.server_close()
        with override_settings(IDENTIDADE_URL=f"http://127.0.0.1:{porta}"), \
                self.assertLogs("identidade_client", level="WARNING"):
            self.assertEqual(self.sinal().status_code, 204)

    def test_o_caminho_nao_escreve_no_banco(self):
        """Sem histórico: nem INSERT, nem UPDATE, nem DELETE, do token ao Conecta."""
        self.responde("POST", "/api/v1/presenca", 204, {})
        with CaptureQueriesContext(connection) as consultas:
            self.assertEqual(self.sinal().status_code, 204)

        self.assertEqual(len(self.sinais_recebidos()), 1, "o sinal foi ao Conecta")
        sql = [c["sql"] for c in consultas.captured_queries]
        self.assertTrue(sql, "a sessão é lida do banco: sem consulta nenhuma, o teste não mediu nada")
        self.assertEqual([s for s in sql if ESCRITA.match(s)], [])

    def test_nem_com_o_conecta_fora(self):
        livre = http.server.HTTPServer(("127.0.0.1", 0), _ConectaFalso)
        porta = livre.server_address[1]
        livre.server_close()
        with override_settings(IDENTIDADE_URL=f"http://127.0.0.1:{porta}"), \
                self.assertLogs("identidade_client", level="WARNING"), \
                CaptureQueriesContext(connection) as consultas:
            self.assertEqual(self.sinal().status_code, 204)
        self.assertEqual([c["sql"] for c in consultas.captured_queries if ESCRITA.match(c["sql"])], [])

    def test_conta_sem_vinculo_nao_vai(self):
        """A conta local sem vínculo (o `createsuperuser`) não tem identidade no Conecta."""
        avulsa = User.objects.create_user(username="avulsa@sejaap.com.br", email="avulsa@sejaap.com.br")
        resposta = cliente_com_sessao(avulsa).post(ROTA, SINAL, format="json")
        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(self.sinais_recebidos(), [])

    def test_e_o_cliente_1_6_0(self):
        self.assertEqual(identidade_client.VERSAO, "1.6.0")
        self.assertTrue(callable(identidade_client.enviar_presenca))
