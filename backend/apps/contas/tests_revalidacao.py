"""
A sessão aberta e o login central em manutenção (cliente 1.5, TSK-1238).

Decisões do dono, de 23/10/2026, que estes testes seguram:

- desativar a pessoa no Conecta derruba a sessão aberta no Lobby: o token de
  quem autoriza negociação para de valer, e a venda negociada volta a pedir
  autorização;
- falha ou manutenção do Conecta NUNCA derrubam a sessão;
- em manutenção, o login responde um 503 que a tela reconhece
  (`erro: "manutencao"`, com a `mensagem` e a `previsao` do aviso), e não o
  "indisponível" de sempre.

A primeira metade troca o `ClienteIdentidade` por um mock, como o resto da
suíte. A segunda fala com um servidor HTTP falso, no loopback, para passar pelo
cliente de verdade: o 503 de manutenção só vira `IdentidadeEmManutencao` lá
dentro, na tradução da resposta. Nada aqui chama servidor de fora.
"""
import http.server
import json
import threading
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from identidade_client import (
    VERSAO,
    IdentidadeEmManutencao,
    IdentidadeIndisponivel,
    NaoEncontrado,
)

from apps.catalogo.tests_escrita import cria_pessoa
from apps.vendas.tests_comprovante import venda_de_tabela

from .models import Papel
from .views import MSG_MANUTENCAO

# Onde a `revalidar_sessao` instancia o cliente: no módulo dele.
ALVO = "identidade_client.ClienteIdentidade"
CHAVE = "AppKey lobby:segredo-de-teste"


def _venda_negociada():
    return venda_de_tabela(negociado=True, valor_total=120000, valor_mensal=10000,
                           entrada=10000, cronograma=[10000] * 11)


@override_settings(AUTH_CENTRAL_ATIVO=True, IDENTIDADE_APP_KEY=CHAVE)
class RevalidacaoDoTokenTest(TestCase):
    """O token emitido pelo /api/sessao, conferido de novo no Conecta."""

    def setUp(self):
        # A resposta do Conecta fica guardada no cache por 5 minutos, por
        # pessoa; sem limpar, um teste herdaria a do anterior.
        cache.clear()
        self.gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        token = RefreshToken.for_user(self.gerente).access_token
        self.cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.identidade_id = self.gerente.vinculo_identidade.identidade_id

    def sessao(self):
        return self.cliente.get("/api/sessao/atual")

    # ----- o que derruba -------------------------------------------------

    def test_identidade_desativada_no_conecta_derruba_a_sessao(self):
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.return_value = {"ativo": False}
            resposta = self.sessao()

        self.assertEqual(resposta.status_code, 401)
        self.assertIn("erro", resposta.json())
        Cliente.return_value.obter.assert_called_once_with(self.identidade_id)

    def test_acesso_revogado_derruba_a_sessao(self):
        """404 `nao_encontrado`: a identidade não tem mais acesso ao Lobby."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.side_effect = NaoEncontrado("x")
            self.assertEqual(self.sessao().status_code, 401)

    def test_a_recusa_fica_guardada_e_nao_vira_uma_chamada_por_requisicao(self):
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.return_value = {"ativo": False}
            self.sessao()
            self.assertEqual(self.sessao().status_code, 401)
        self.assertEqual(Cliente.return_value.obter.call_count, 1)

    def test_venda_negociada_com_o_token_de_quem_foi_desativado_volta_a_pedir_autorizacao(self):
        """É o token que destrava o desconto: aqui a revalidação mais importa.

        O comprovante não dá 401 (o `JWTOpcional` trata token ruim como "sem
        autorização"), mas a venda negociada é recusada com a frase que manda
        pedir a autorização de novo.
        """
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.return_value = {"ativo": False}
            resposta = self.cliente.post(
                "/api/venda/comprovante", _venda_negociada(), format="json"
            )

        self.assertEqual(resposta.status_code, 422)
        self.assertIn("exigem a autorização", resposta.json()["erro"])

    def test_reativada_e_logada_de_novo_o_token_novo_vale_na_hora(self):
        """Recusa → reativação → novo login → token novo, sem limpar o cache.

        O login aceito pelo /api/sessao conta como revalidação (como no
        `BackendIdentidade`): sem isso, a recusa guardada valeria para o token
        novo pelo resto dos 5 minutos, e a venda negociada seria recusada logo
        depois de o modal dizer "autorizado".
        """
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.return_value = {"ativo": False}
            self.assertEqual(self.sessao().status_code, 401)

        with patch("apps.contas.views.ClienteIdentidade") as Login:
            Login.return_value.verificar.return_value = {
                "identidade_id": str(self.identidade_id),
                "email": "gerente@sejaap.com.br",
                "nome": "Gerente",
                "precisa_trocar_senha": False,
            }
            resposta = APIClient().post(
                "/api/sessao", {"email": "gerente@sejaap.com.br", "senha": "x"}, format="json"
            )
        self.assertEqual(resposta.status_code, 200)

        novo = APIClient(HTTP_AUTHORIZATION=f"Bearer {resposta.json()['token']}")
        with patch(ALVO) as Cliente:
            self.assertEqual(novo.get("/api/sessao/atual").status_code, 200)
            self.assertEqual(
                novo.post("/api/venda/comprovante", _venda_negociada(), format="json").status_code,
                200,
            )
        # Valeu pelo login, sem perguntar de novo ao Conecta.
        Cliente.return_value.obter.assert_not_called()

    def test_o_login_chama_a_funcao_publica_do_cliente(self):
        """`lembrar_sessao_valida` é pública desde a 1.5.3 (TSK-1271).

        A privada `_lembrar_sessao_valida` sai na próxima versão do cliente: a
        view tem de chamar a pública, e este teste quebra se voltar à outra.
        """
        with (
            patch("apps.contas.views.ClienteIdentidade") as Login,
            patch("identidade_client.lembrar_sessao_valida") as lembrar,
            # `create=True`: quando o apelido sair do cliente, o teste segue
            # valendo, em vez de quebrar no `patch`.
            patch("identidade_client._lembrar_sessao_valida", create=True) as privada,
        ):
            Login.return_value.verificar.return_value = {
                "identidade_id": str(self.identidade_id),
                "email": "gerente@sejaap.com.br",
                "nome": "Gerente",
                "precisa_trocar_senha": False,
            }
            resposta = APIClient().post(
                "/api/sessao", {"email": "gerente@sejaap.com.br", "senha": "x"}, format="json"
            )

        self.assertEqual(resposta.status_code, 200)
        lembrar.assert_called_once()
        self.assertEqual(lembrar.call_args.args[0].email, "gerente@sejaap.com.br")
        privada.assert_not_called()

    def test_login_recusado_nao_conta_como_revalidacao(self):
        """Só o login aceito vale: chamada em outro ponto daria por conferida uma
        sessão que ninguém conferiu (docstring de `lembrar_sessao_valida`)."""
        from identidade_client import CredencialInvalida

        with (
            patch("apps.contas.views.ClienteIdentidade") as Login,
            patch("identidade_client.lembrar_sessao_valida") as lembrar,
        ):
            Login.return_value.verificar.side_effect = CredencialInvalida("Credenciais inválidas.")
            resposta = APIClient().post(
                "/api/sessao", {"email": "gerente@sejaap.com.br", "senha": "x"}, format="json"
            )

        self.assertEqual(resposta.status_code, 401)
        lembrar.assert_not_called()

    # ----- o que NÃO derruba ---------------------------------------------

    def test_identidade_ativa_segue_e_so_pergunta_uma_vez_a_cada_5_minutos(self):
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.return_value = {"ativo": True}
            self.assertEqual(self.sessao().status_code, 200)
            self.assertEqual(self.sessao().status_code, 200)
        self.assertEqual(Cliente.return_value.obter.call_count, 1)

    def test_conecta_fora_do_ar_mantem_a_sessao(self):
        """Sessão derrubada por falha do Conecta seria todo mundo fora de uma vez."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.side_effect = IdentidadeIndisponivel("x")
            self.assertEqual(self.sessao().status_code, 200)

    def test_conecta_em_manutencao_mantem_a_sessao(self):
        """Quem já está logado segue normal (decisão do dono, 23/10/2026)."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.side_effect = IdentidadeEmManutencao(
                "manutenção", mensagem="Atualização do sistema"
            )
            self.assertEqual(self.sessao().status_code, 200)

    def test_venda_negociada_segue_com_o_conecta_fora_do_ar(self):
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.side_effect = IdentidadeIndisponivel("x")
            resposta = self.cliente.post(
                "/api/venda/comprovante", _venda_negociada(), format="json"
            )
        self.assertEqual(resposta.status_code, 200)

    def test_redis_fora_mantem_a_sessao_sem_martelar_o_conecta(self):
        """O cache `default` do Lobby falha aberto no `get` e no `set`, mas não no
        `add` (`config/cache.py`): com o Redis fora, o `add` levanta, e a
        `revalidar_sessao` mantém a sessão sem perguntar nada. Sem isso, cada
        requisição viraria uma chamada ao Conecta enquanto o Redis estivesse
        fora.
        """
        cache_fora = MagicMock()
        cache_fora.get.return_value = None
        cache_fora.add.side_effect = ConnectionError("Redis fora")
        # O registro da falha sai no máximo uma vez por minuto por processo:
        # zerado aqui, para o teste não depender de quem rodou antes.
        with patch("identidade_client.cache", cache_fora), patch(ALVO) as Cliente, \
                patch("identidade_client._ULTIMO_REGISTRO_DE_FALHA", None):
            with self.assertLogs("identidade_client", level="ERROR"):
                self.assertEqual(self.sessao().status_code, 200)
        Cliente.return_value.obter.assert_not_called()

    @override_settings(AUTH_CENTRAL_ATIVO=False)
    def test_com_a_central_desligada_nao_pergunta_nada(self):
        with patch(ALVO) as Cliente:
            self.assertEqual(self.sessao().status_code, 200)
        Cliente.return_value.obter.assert_not_called()


@override_settings(AUTH_CENTRAL_ATIVO=True, IDENTIDADE_APP_KEY=CHAVE)
class RevalidacaoDoDjangoAdminTest(TestCase):
    """A sessão do /django-admin/, da diretoria, cai do mesmo jeito."""

    def setUp(self):
        cache.clear()
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.client.force_login(self.diretoria)

    def test_identidade_desativada_derruba_a_sessao_do_admin(self):
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.return_value = {"ativo": False}
            resposta = self.client.get("/django-admin/")
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/django-admin/login/", resposta["Location"])

    def test_conecta_fora_do_ar_mantem_a_sessao_do_admin(self):
        with patch(ALVO) as Cliente:
            Cliente.return_value.obter.side_effect = IdentidadeIndisponivel("x")
            self.assertEqual(self.client.get("/django-admin/").status_code, 200)


# ----- contra um servidor falso, com o cliente de verdade ---------------------

class _ConectaFalso(http.server.BaseHTTPRequestHandler):
    """Responde o que o teste mandou, e anota o que recebeu."""

    respostas = {}   # (método, caminho) -> (status, corpo)
    recebidas = []

    def _responder(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        type(self).recebidas.append({
            "metodo": self.command,
            "caminho": self.path,
            "cabecalhos": dict(self.headers),
            "corpo": json.loads(corpo) if corpo else None,
        })
        codigo, dados = type(self).respostas.get(
            (self.command, self.path), (500, {"erro": "?"})
        )
        bruto = json.dumps(dados).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(bruto)))
        self.end_headers()
        self.wfile.write(bruto)

    do_GET = do_POST = _responder

    def log_message(self, *args):
        pass


MANUTENCAO = {
    "erro": "manutencao",
    "detalhe": "O login central está em manutenção.",
    "mensagem": "Atualização do sistema. Voltamos logo.",
    "previsao": "2026-10-26T15:00:00-03:00",
}


class ConectaFalsoTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ConectaFalso)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.servidor.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        _ConectaFalso.respostas = {}
        _ConectaFalso.recebidas = []
        configuracao = override_settings(
            AUTH_CENTRAL_ATIVO=True, IDENTIDADE_URL=self.url, IDENTIDADE_APP_KEY=CHAVE
        )
        configuracao.enable()
        self.addCleanup(configuracao.disable)

    def responde(self, metodo, caminho, codigo, dados):
        _ConectaFalso.respostas[(metodo, caminho)] = (codigo, dados)


class ManutencaoNoLoginTest(ConectaFalsoTest):
    def entrar(self):
        return APIClient().post(
            "/api/sessao", {"email": "gerente@sejaap.com.br", "senha": "x"}, format="json"
        )

    def test_login_em_manutencao_responde_o_503_que_a_tela_reconhece(self):
        self.responde("POST", "/api/v1/credenciais/verificar", 503, MANUTENCAO)
        resposta = self.entrar()

        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json(), {
            "erro": "manutencao",
            "mensagem": "Atualização do sistema. Voltamos logo.",
            "previsao": "2026-10-26T15:00:00-03:00",
        })

    def test_manutencao_sem_previsao_e_sem_texto_ainda_e_manutencao(self):
        self.responde("POST", "/api/v1/credenciais/verificar", 503, {"erro": "manutencao"})
        corpo = self.entrar().json()

        self.assertEqual(corpo["erro"], "manutencao")
        self.assertIsNone(corpo["previsao"])
        # Sem `mensagem`, o cliente põe a frase genérica do `detalhe` dele.
        self.assertTrue(corpo["mensagem"])

    def test_aviso_com_textos_vazios_usa_a_frase_do_lobby(self):
        self.responde("POST", "/api/v1/credenciais/verificar", 503,
                      {"erro": "manutencao", "detalhe": "", "mensagem": "  "})
        corpo = self.entrar().json()

        self.assertEqual(corpo["mensagem"], MSG_MANUTENCAO)

    def test_503_que_nao_e_manutencao_continua_indisponivel(self):
        """O Conecta ID de hoje não tem manutenção: o 503 dele é o de sempre."""
        self.responde("POST", "/api/v1/credenciais/verificar", 503, {"erro": "indisponivel"})
        with self.assertLogs("apps.contas.views", level="ERROR"):
            resposta = self.entrar()

        self.assertEqual(resposta.status_code, 503)
        self.assertIn("indisponível", resposta.json()["erro"])

    def test_o_cliente_se_apresenta_com_a_versao_e_a_chave(self):
        """A Cloudflare barra o `Python-urllib` (desde a 1.5.2)."""
        self.responde("POST", "/api/v1/credenciais/verificar", 503, MANUTENCAO)
        self.entrar()

        cabecalhos = _ConectaFalso.recebidas[0]["cabecalhos"]
        self.assertEqual(cabecalhos["User-Agent"], f"ConectaIdentidadeClient/{VERSAO}")
        self.assertEqual(cabecalhos["Authorization"], CHAVE)
        self.assertEqual(VERSAO, "1.5.3")

    def test_esqueci_senha_em_manutencao(self):
        self.responde("POST", "/api/v1/senha/esqueci", 503, MANUTENCAO)
        resposta = APIClient().post(
            "/api/senha/esqueci", {"email": "gerente@sejaap.com.br"}, format="json"
        )
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json()["erro"], "manutencao")

    def test_definir_senha_em_manutencao(self):
        self.responde("POST", "/api/v1/senha/definir", 503, MANUTENCAO)
        resposta = APIClient().post(
            "/api/senha/definir", {"token": "t" * 40, "senha_nova": "Uma-Senha-Boa-42"},
            format="json",
        )
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json()["mensagem"], MANUTENCAO["mensagem"])


class RevalidacaoContraOServidorTest(ConectaFalsoTest):
    def setUp(self):
        super().setUp()
        self.gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        token = RefreshToken.for_user(self.gerente).access_token
        self.cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.caminho = f"/api/v1/identidades/{self.gerente.vinculo_identidade.identidade_id}"

    def sessao(self):
        return self.cliente.get("/api/sessao/atual")

    def test_ativa_segue(self):
        self.responde("GET", self.caminho, 200, {"ativo": True})
        self.assertEqual(self.sessao().status_code, 200)
        self.assertEqual(_ConectaFalso.recebidas[0]["caminho"], self.caminho)

    def test_desativada_cai(self):
        self.responde("GET", self.caminho, 200, {"ativo": False})
        self.assertEqual(self.sessao().status_code, 401)

    def test_sem_acesso_ao_lobby_cai(self):
        self.responde("GET", self.caminho, 404, {"erro": "nao_encontrado"})
        self.assertEqual(self.sessao().status_code, 401)

    def test_em_manutencao_fica(self):
        self.responde("GET", self.caminho, 503, MANUTENCAO)
        self.assertEqual(self.sessao().status_code, 200)

    def test_fora_do_ar_fica(self):
        # Uma porta em que ninguém escuta: a do servidor falso, já liberada.
        livre = http.server.HTTPServer(("127.0.0.1", 0), _ConectaFalso)
        porta = livre.server_address[1]
        livre.server_close()
        with override_settings(IDENTIDADE_URL=f"http://127.0.0.1:{porta}"):
            self.assertEqual(self.sessao().status_code, 200)

    def test_recusa_de_chave_fica(self):
        """Erro de integração (chave recusada) não é a pessoa desativada."""
        self.responde("GET", self.caminho, 403, {"erro": "app_nao_autorizado"})
        self.assertEqual(self.sessao().status_code, 200)


class SemEscritaDeGestaoTest(TestCase):
    """O Lobby não cria login nem concede acesso: isso é só do Conecta.

    Os métodos saíram do cliente na 1.5. Se alguém recopiar uma versão antiga,
    este teste avisa antes de um fluxo de gestão voltar a existir aqui.
    """

    def test_o_cliente_nao_tem_os_metodos_de_gestao(self):
        from identidade_client import ClienteIdentidade

        for metodo in ("criar", "alterar", "conceder_acesso", "configurar_acesso",
                       "revogar_acesso", "emitir_token_de_senha"):
            with self.subTest(metodo=metodo):
                self.assertFalse(hasattr(ClienteIdentidade, metodo))
