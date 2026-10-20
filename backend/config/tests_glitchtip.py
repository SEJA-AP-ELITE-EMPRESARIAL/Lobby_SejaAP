"""
Erros do backend no GlitchTip sem dado pessoal (TSK-1142).

Três garantias do contrato, cada uma com os seus testes:

1. O `before_send` (`config.glitchtip.limpar_evento`) mascara CNPJ, CPF, e-mail,
   senha, token, o cabeçalho Authorization, cookies e o corpo da requisição.
2. Na suíte o SDK fica desligado: nada sai da máquina, com ou sem SENTRY_DSN.
3. Ligado, o evento de uma exceção real sai com o release pedido e já limpo. Este
   teste troca o transporte do SDK por um que só guarda o envelope, então também
   não usa rede.

Espelha o `tests/test_glitchtip.py` do Conecta (TSK-1136), mais o que é do Lobby:
o `AppKey` do Conecta ID, a URL com senha do banco e do Redis, a chave do Omie, a
asserção do Conecta e o comprovante de venda.
"""
import json
import time
from unittest import mock

import sentry_sdk
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.handlers.wsgi import WSGIHandler
from django.core.signals import request_finished, request_started
from django.db import close_old_connections
from django.test import SimpleTestCase, TestCase
from django.test.client import RequestFactory
from rest_framework_simplejwt.tokens import RefreshToken
from sentry_sdk.transport import Transport

from config import glitchtip
from config.glitchtip import FILTRADO, limpar_evento, mascarar_texto

CNPJ = "12.345.678/0001-95"
CNPJ_LIMPO = "12345678000195"
CPF = "123.456.789-09"
EMAIL = "fulana.cliente@empresa.com.br"
SENHA = "Senha-Secreta-123"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjo0Mn0.c2lnbmF0dXJh"
ASSERCAO = "eyJhbGciOiJFZERTQSIsImtpZCI6ImNvbmVjdGEifQ.eyJzdWIiOiJ4In0.YXNzaW5hdHVyYQ"


def _evento_com_dado_pessoal():
    """Um evento no formato do SDK do Django, com dado pessoal em todo canto."""
    return {
        "message": f"Falha na venda do cliente {CNPJ} ({EMAIL})",
        "exception": {"values": [{
            "type": "IntegrityError",
            "value": f"Key (cnpj)=({CNPJ_LIMPO}) already exists; cpf {CPF}",
            "stacktrace": {"frames": [{
                "filename": "apps/vendas/views.py",
                "function": "emitir_comprovante",
                "vars": {"payload": f"<Venda {CNPJ}>", "senha": SENHA},
            }]},
        }]},
        "request": {
            "method": "POST",
            "url": f"https://lobby.sejaap.com.br/api/venda/comprovante?cnpj={CNPJ_LIMPO}&email={EMAIL}",
            "query_string": f"cnpj={CNPJ_LIMPO}&email={EMAIL}",
            "headers": {
                "Authorization": f"Bearer {JWT}",
                "Cookie": "sessionid=abc123; csrftoken=def456",
                "X-Conecta-Assercao": ASSERCAO,
                "X-Forwarded-For": "200.200.200.200",
                "CF-Connecting-IP": "200.200.200.200",
                "X-Real-IP": "200.200.200.200",
                "User-Agent": "Mozilla/5.0",
                "Referer": f"https://lobby.sejaap.com.br/definir-senha?token={SENHA}",
            },
            "cookies": {"sessionid": "abc123"},
            "data": {"email": EMAIL, "senha": SENHA, "cnpj": CNPJ,
                     "parcelas": [{"valor": 1234.56, "vencimento": "2026-11-15"}]},
            "env": {"REMOTE_ADDR": "200.200.200.200"},
        },
        "user": {"id": 42, "email": EMAIL, "ip_address": "200.200.200.200"},
        "extra": {"password": SENHA, "detalhe": f"telefone (11) 91234-5678, token {JWT}"},
        "breadcrumbs": {"values": [{
            "category": "httplib",
            "data": {"url": f"https://n8n.sejaap.com.br/webhook/brasilapi-cnpj?cnpj={CNPJ_LIMPO}",
                     "http.query": f"cnpj={CNPJ_LIMPO}"},
        }]},
    }


class LimparEventoTests(SimpleTestCase):
    """Passo 1 do contrato: CNPJ, senha e Authorization saem mascarados (e o resto da lista)."""

    def setUp(self):
        self.limpo = limpar_evento(_evento_com_dado_pessoal())
        self.texto = json.dumps(self.limpo, ensure_ascii=False)

    def test_cnpj_senha_e_authorization_saem_mascarados(self):
        """O passo 1 do contrato, ao pé da letra."""
        for dado in (CNPJ, CNPJ_LIMPO, SENHA, JWT, f"Bearer {JWT}"):
            with self.subTest(dado=dado):
                self.assertNotIn(dado, self.texto)
        self.assertNotIn("Authorization", self.limpo["request"]["headers"])
        self.assertIn("[CNPJ]", self.limpo["message"])
        self.assertEqual(self.limpo["extra"]["password"], FILTRADO)

    def test_nenhum_dado_pessoal_sobra_em_lugar_nenhum_do_evento(self):
        for dado in (CNPJ, CNPJ_LIMPO, CPF, EMAIL, SENHA, JWT, ASSERCAO, "abc123", "def456",
                     "200.200.200.200", "91234-5678", "1234.56", "2026-11-15"):
            with self.subTest(dado=dado):
                self.assertNotIn(dado, self.texto)

    def test_cnpj_cpf_e_email_viram_marcadores_legiveis(self):
        valor = self.limpo["exception"]["values"][0]["value"]
        self.assertEqual(valor, "Key (cnpj)=([Filtrado]) already exists; cpf [CPF]")
        self.assertEqual(self.limpo["message"], "Falha na venda do cliente [CNPJ] ([e-mail])")

    def test_authorization_cookie_e_assercao_nao_saem_e_o_resto_e_lista_fechada(self):
        cabecalhos = self.limpo["request"]["headers"]
        self.assertEqual(set(cabecalhos), {"User-Agent", "Referer"})
        self.assertEqual(cabecalhos["Referer"], "https://lobby.sejaap.com.br/definir-senha")

    def test_corpo_cookies_query_e_ambiente_da_requisicao_somem(self):
        requisicao = self.limpo["request"]
        for campo in ("data", "cookies", "query_string", "env"):
            with self.subTest(campo=campo):
                self.assertNotIn(campo, requisicao)
        self.assertEqual(requisicao["url"], "https://lobby.sejaap.com.br/api/venda/comprovante")

    def test_usuario_e_variaveis_de_frame_nao_saem(self):
        self.assertNotIn("user", self.limpo)
        frame = self.limpo["exception"]["values"][0]["stacktrace"]["frames"][0]
        self.assertNotIn("vars", frame)
        self.assertEqual(frame["function"], "emitir_comprovante")

    def test_chave_sensivel_tem_o_valor_trocado(self):
        self.assertEqual(self.limpo["extra"]["password"], FILTRADO)
        self.assertEqual(self.limpo["extra"]["detalhe"], "telefone [telefone], token [token]")

    def test_rastro_de_http_sai_sem_query(self):
        dados = self.limpo["breadcrumbs"]["values"][0]["data"]
        self.assertEqual(dados["url"], "https://n8n.sejaap.com.br/webhook/brasilapi-cnpj")
        self.assertNotIn("http.query", dados)

    def test_credenciais_do_lobby_em_texto(self):
        """O que é do Lobby: AppKey do Conecta ID, URL com senha, chave do Omie, comprovante."""
        casos = {
            "Authorization: AppKey lobby:s3gr3d0-do-app": "Authorization: AppKey [token]",
            "postgresql://lobby_app:S3nh4Banco@db-tunnel:5437/lobby":
                "postgresql://[credencial]@db-tunnel:5437/lobby",
            "redis://:s3nh4redis@redis:6379/0": "redis://[credencial]@redis:6379/0",
            "https://chavepublica@erros.sejaap.com.br/3": "https://[credencial]@erros.sejaap.com.br/3",
            '{"call": "ListarDepartamentos", "app_key": "123456", "app_secret": "abcdef"}':
                '{"call": "ListarDepartamentos", "app_key": "[Filtrado]", "app_secret": "[Filtrado]"}',
            '{"comprovante": "v1.abc.def", "nonce": "n0nc3"}':
                '{"comprovante": "[Filtrado]", "nonce": "[Filtrado]"}',
            "{'razao_social': 'Padaria Pao Quente LTDA', 'representante': 'Joao'}":
                "{'razao_social': '[Filtrado]', 'representante': '[Filtrado]'}",
            "email joão.silva@empresa.com.br": "email [e-mail]",
            "CPF 123456789-09": "CPF [CPF]",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto[:30]):
                self.assertEqual(mascarar_texto(texto), esperado)

    def test_erro_do_postgres_perde_a_linha_e_o_valor_da_chave(self):
        """Nome, endereço e telefone sem máscara: regex não acha nome, então o DETAIL some."""
        nao_nulo = (
            'null value in column "categoria_id" of relation "catalogo_produto" violates '
            "not-null constraint\nDETAIL:  Failing row contains (90001, Padaria Pao Quente LTDA, "
            "Joao da Silva, Rua das Flores 123, 11 91234-5678, 1132345678)."
        )
        limpo = mascarar_texto(nao_nulo)
        self.assertTrue(limpo.endswith("DETAIL: [Filtrado]"), limpo)
        for dado in ("Padaria", "Joao", "Flores", "91234", "1132345678"):
            self.assertNotIn(dado, limpo)
        self.assertEqual(
            mascarar_texto('duplicate key value violates unique constraint "x" Key (sigla)=(PRO) already exists.'),
            'duplicate key value violates unique constraint "x" Key (sigla)=([Filtrado]) already exists.',
        )
        self.assertEqual(mascarar_texto("Failing row contains (1, Maria)."), "Failing row contains ([Filtrado])")
        self.assertEqual(mascarar_texto("ligar +55 11 912345678 ou 11 91234-5678"),
                         "ligar [telefone] ou [telefone]")

    def test_chave_sensivel_em_texto(self):
        evento = limpar_evento({
            "exception": {"values": [{"type": "ValueError",
                                      "value": f'corpo {{"senha": "{SENHA}", "nome": "ok"}} senha={SENHA}&x=1'}]},
        })
        self.assertEqual(evento["exception"]["values"][0]["value"],
                         'corpo {"senha": "[Filtrado]", "nome": "ok"} senha=[Filtrado]&x=1')

    def test_repr_de_dict_e_argumento_nomeado_com_aspas(self):
        self.assertEqual(mascarar_texto("{'senha': 'S3cr3t!x', 'nome': 'ok'}"),
                         "{'senha': '[Filtrado]', 'nome': 'ok'}")
        self.assertEqual(mascarar_texto('Usuario(senha=\'S3cr3t!x\', token="abc")'),
                         "Usuario(senha=[Filtrado], token=[Filtrado])")

    def test_linha_de_codigo_passa_pelas_mascaras_de_dado_e_nao_pelas_de_chave(self):
        frames = [{
            "context_line": '    enviar_aviso("fulana@empresa.com.br", "123.456.789-09")',
            "pre_context": [
                '    CNPJ_TESTE = "12.345.678/0001-95"; FONE = "(11) 91234-5678"',
                f'    cabecalho = "Bearer abc123"; jwt = "{JWT}"',
            ],
            # Com as máscaras de chave, as duas virariam `senha=[Filtrado]` e `cnpj=[Filtrado]`.
            "post_context": ["    verificar(senha=senha_digitada)",
                             "    venda = Venda.objects.filter(cnpj=cnpj_limpo).first()"],
        }]
        evento = limpar_evento({"exception": {"values": [{"stacktrace": {"frames": frames}}]}})
        frame = evento["exception"]["values"][0]["stacktrace"]["frames"][0]
        self.assertEqual(frame["context_line"], '    enviar_aviso("[e-mail]", "[CPF]")')
        self.assertEqual(frame["pre_context"], [
            '    CNPJ_TESTE = "[CNPJ]"; FONE = "[telefone]"',
            '    cabecalho = "Bearer [token]"; jwt = "[token]"',
        ])
        self.assertEqual(frame["post_context"], frames[0]["post_context"])

    def test_dado_na_posicao_do_corte_nao_sai_pela_metade(self):
        """
        CPF, e-mail e JWT partidos em cada posição possível, nos três cortes: o de antes
        das máscaras, o final e o do `max_value_length` do SDK (que chega com '...').
        """
        def em_volta(dado, posicao, tamanho):
            antes = ("x " * tamanho)[:posicao]
            return antes + dado + " fim" * (tamanho // 4)

        limite_bruto = glitchtip._TAMANHO_MAXIMO + glitchtip._FOLGA_DAS_MASCARAS
        sdk = glitchtip.MAX_VALUE_LENGTH - len("...")
        for dado in ("123.456.789-09", "fulana.cliente@empresa.com.br", JWT):
            for parte in range(1, len(dado)):
                casos = {
                    "antes das máscaras": em_volta(dado, limite_bruto - parte, limite_bruto),
                    "corte final": em_volta(dado, glitchtip._TAMANHO_MAXIMO - parte, limite_bruto),
                    "sdk": em_volta(dado, sdk - parte, sdk)[:sdk] + "...",
                }
                for corte, texto in casos.items():
                    with self.subTest(dado=dado[:6], parte=parte, corte=corte):
                        limpo = mascarar_texto(texto)
                        self.assertTrue(limpo.endswith(" [cortado]"))
                        self.assertNotIn(dado[:parte], limpo)

    def test_valor_sensivel_aberto_no_corte_sai_inteiro(self):
        """Senha e nome cujo fechamento ficou depois do corte do SDK (strip_string real)."""
        from sentry_sdk.utils import strip_string

        limite = glitchtip.MAX_VALUE_LENGTH
        # Com escape (o json.dumps padrão escreve acento como \u00e7) o corte pode cair
        # logo depois da barra; e o nome pode ter parêntese dentro.
        for valor in (' {"senha": "Minha senha#2024!"}', " Usuario(senha='Minha senha#2024!')",
                      " Key (nome)=(Joana Silva Pereira) already exists",
                      ' {"senha": "Minha\\u00e7\\u00e3oForte"}', " {'senha': 'Minha\\nSenhaForte'}",
                      " Key (nome)=(Joana (filha) Silva) already exists"):
            for parte in range(2, len(valor)):
                texto = ("x " * limite)[:limite - 3 - (len(valor) - parte)] + valor + " fim" * 3000
                with self.subTest(valor=valor[:12], parte=parte):
                    limpo = mascarar_texto(strip_string(texto, limite).value)
                    self.assertNotIn("Minha", limpo)
                    self.assertNotIn("Joana", limpo)

    def test_pior_caso_das_regex_dentro_do_limite(self):
        """Entradas montadas para o backtracking, do tamanho que as regex recebem (16 KB + folga)."""
        tamanho = glitchtip._TAMANHO_MAXIMO + glitchtip._FOLGA_DAS_MASCARAS
        casos = {
            "json": ('"token' * tamanho)[:tamanho],
            "formulario": ("token=" * tamanho)[:tamanho],
            "repr": ("{'token': 'a', " * tamanho)[:tamanho],
            "chave do postgres": ("Key (" * tamanho)[:tamanho],
            "alfanumerico": "a" * tamanho,
            "email": ("a." * tamanho)[:tamanho],
            "url com credencial": ("a://" * tamanho)[:tamanho],
            "url sem arroba": "https://" + "a" * tamanho,
            "appkey": ("AppKey " * tamanho)[:tamanho],
        }
        for nome, texto in casos.items():
            with self.subTest(caso=nome):
                inicio = time.monotonic()
                mascarar_texto(texto)
                # Folga para o runner lento do CI: o quadrático custaria segundos.
                self.assertLess(time.monotonic() - inicio, 0.5)
        self.assertEqual(mascarar_texto("Periodo 01 2025-2026"), "Periodo 01 2025-2026")

    def test_mascara_cnpj_alfanumerico_e_nao_estraga_texto_comum(self):
        self.assertEqual(mascarar_texto("cnpj 12ABC34501DE35"), "cnpj [CNPJ]")
        self.assertEqual(mascarar_texto("12.ABC.345/01DE-35"), "[CNPJ]")
        comum = 'ValueError: invalid literal for int() with base 10: "abc" (linha 2026)'
        self.assertEqual(mascarar_texto(comum), comum)
        # Protocolo de venda e URL do webhook não são dado pessoal: ficam legíveis.
        self.assertEqual(mascarar_texto("protocolo PRO-260701005821"), "protocolo PRO-260701005821")
        self.assertEqual(mascarar_texto("https://n8n.sejaap.com.br/webhook/lobby-apn"),
                         "https://n8n.sejaap.com.br/webhook/lobby-apn")


class SDKDesligadoNaSuiteTests(SimpleTestCase):
    """Passo 2 do contrato: a suíte roda sem SENTRY_DSN e nada é enviado."""

    def test_settings_nao_ligou_o_sdk(self):
        self.assertFalse(settings.GLITCHTIP_ATIVO)
        self.assertFalse(sentry_sdk.get_client().is_active())

    def test_sem_dsn_iniciar_nao_chama_o_sdk(self):
        with mock.patch("sentry_sdk.init") as init:
            self.assertFalse(glitchtip.iniciar(""))
            self.assertFalse(glitchtip.iniciar("   ", release="abc1234"))
            self.assertFalse(glitchtip.iniciar(None))
        init.assert_not_called()

    def test_dsn_malformado_nao_derruba_o_django(self):
        for dsn in ("https://erros.exemplo.invalid/2", "erros.exemplo.invalid/2"):
            with self.subTest(dsn=dsn), mock.patch("builtins.print") as aviso:
                self.assertFalse(glitchtip.iniciar(dsn))
            aviso.assert_called_once_with("[GLITCHTIP] desligado: SENTRY_DSN inválido (BadDsn)", flush=True)
        self.assertFalse(sentry_sdk.get_client().is_active())

    def test_na_suite_nem_com_dsn_no_ambiente_o_sdk_liga(self):
        ambiente = {"SENTRY_DSN": "https://chave@erros.exemplo.invalid/1", "GIT_COMMIT": "abc1234"}
        with mock.patch("config.glitchtip.iniciar") as iniciar:
            for argv in (["manage.py", "test"], ["manage.py", "test", "config"]):
                with self.subTest(argv=argv):
                    self.assertFalse(glitchtip.ligar_pelo_ambiente(ambiente, argv=argv, modulos={}))
            self.assertFalse(glitchtip.ligar_pelo_ambiente(
                ambiente, argv=["gunicorn"], modulos={"pytest": object()}))
            self.assertFalse(glitchtip.ligar_pelo_ambiente(
                {**ambiente, "PYTEST_CURRENT_TEST": "x"}, argv=["gunicorn"], modulos={}))
        iniciar.assert_not_called()

    def test_fora_da_suite_liga_com_o_release_do_git_commit(self):
        ambiente = {"SENTRY_DSN": " https://chave@erros.exemplo.invalid/1 ", "GIT_COMMIT": " abc1234 "}
        with mock.patch("config.glitchtip.iniciar", return_value=True) as iniciar:
            self.assertTrue(glitchtip.ligar_pelo_ambiente(
                ambiente, argv=["/usr/local/bin/gunicorn", "config.wsgi:application"], modulos={}))
            # O laço dos departamentos também liga: é o mesmo settings.
            glitchtip.ligar_pelo_ambiente(
                ambiente, argv=["manage.py", "sincronizar_departamentos"], modulos={})
        iniciar.assert_called_with(
            " https://chave@erros.exemplo.invalid/1 ", release="abc1234", environment="production")
        with mock.patch("config.glitchtip.iniciar", return_value=False) as iniciar:
            glitchtip.ligar_pelo_ambiente({"SENTRY_ENVIRONMENT": "homolog"}, argv=["gunicorn"], modulos={})
        iniciar.assert_called_once_with("", release=None, environment="homolog")


class _TransporteNaMemoria(Transport):
    """Guarda os envelopes em vez de mandá-los: o teste não usa rede."""

    def __init__(self, options=None):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


class EventoDeVerdadeTests(TestCase):
    """
    O SDK ligado de verdade, com o transporte trocado: a exceção da rota de diagnóstico
    vira evento com o release pedido, o ambiente de produção e nada do que veio na requisição.
    """

    def setUp(self):
        # O teto da rota de diagnóstico conta no cache, por id de usuário, e o id
        # se repete entre testes: sem limpar, a chamada daqui pode levar 429.
        cache.clear()
        self.addCleanup(cache.clear)
        self.superusuario = User.objects.create_user(
            username="su-glitchtip@test.local", email="su-glitchtip@test.local", is_superuser=True)
        self.transporte = _TransporteNaMemoria()
        with mock.patch("builtins.print"):
            ligou = glitchtip.iniciar(
                "https://chave@erros.exemplo.invalid/1", release="abc1234", transport=self.transporte)
        self.assertTrue(ligou)
        self.addCleanup(self._desligar)

    @staticmethod
    def _desligar():
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)

    def _eventos(self):
        sentry_sdk.flush()
        eventos = []
        for envelope in self.transporte.envelopes:
            for item in envelope.items:
                if item.type == "event":
                    eventos.append(item.payload.json)
        return eventos

    def test_opcoes_que_o_contrato_exige(self):
        opcoes = sentry_sdk.get_client().options
        self.assertFalse(opcoes["send_default_pii"])
        self.assertFalse(opcoes["include_local_variables"])
        self.assertEqual(opcoes["max_request_body_size"], "never")
        self.assertEqual(opcoes["max_value_length"], glitchtip.MAX_VALUE_LENGTH)
        self.assertEqual(opcoes["trace_propagation_targets"], [])
        self.assertFalse(opcoes["auto_enabling_integrations"])
        integracoes = set(sentry_sdk.get_client().integrations)
        self.assertIn("django", integracoes)
        self.assertNotIn("redis", integracoes)
        self.assertIsNone(opcoes["traces_sample_rate"])
        self.assertIsNone(opcoes["traces_sampler"])
        self.assertIsNone(opcoes["profiles_sample_rate"])
        self.assertFalse(opcoes["auto_session_tracking"])
        self.assertFalse(opcoes["send_client_reports"])
        self.assertEqual(opcoes["release"], "abc1234")
        self.assertEqual(opcoes["environment"], "production")

    def test_chamada_de_saida_nao_ganha_cabecalho_do_sdk(self):
        """Sem tracing, nada de `sentry-trace`/`baggage` no Conecta ID nem no Omie."""
        import http.server
        import threading
        import urllib.request

        recebidos = {}

        class Destino(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                recebidos.update({k.lower(): v for k, v in self.headers.items()})
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        servidor = http.server.HTTPServer(("127.0.0.1", 0), Destino)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        urllib.request.urlopen(f"http://127.0.0.1:{servidor.server_port}/api/verificar").read()
        self.assertIn("host", recebidos)
        self.assertNotIn("sentry-trace", recebidos)
        self.assertNotIn("baggage", recebidos)
        self.assertNotIn("traceparent", recebidos)

    def test_log_de_erro_nao_vira_evento(self):
        """A linha de log da integração leva IP e e-mail: log não vai ao GlitchTip."""
        import logging

        with self.assertLogs("lobby.integracao", "ERROR"):
            logging.getLogger("lobby.integracao").error("integracao ip=200.200.200.200 sub=%s", EMAIL)
        self.assertEqual(self._eventos(), [])

    def test_excecao_da_rota_chega_limpa_e_com_o_release(self):
        token = str(RefreshToken.for_user(self.superusuario).access_token)
        # Pelo WSGIHandler, como o gunicorn: o Client do Django tem handler próprio e pula a
        # camada WSGI que o SDK usa para anexar a requisição ao evento.
        fabrica = RequestFactory()
        fabrica.cookies["sessionid"] = "cookie-de-sessao-secreto"
        environ = fabrica.post(
            f"/api/diagnostico/erro-de-teste?cnpj={CNPJ_LIMPO}&email={EMAIL}",
            data=json.dumps({"senha": SENHA, "cnpj": CNPJ, "parcelas": [{"valor": 1234.56}]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_FORWARDED_FOR="200.200.200.200",
            HTTP_CF_CONNECTING_IP="200.200.200.200",
        ).environ
        status = []
        # Como o ClientHandler do Django: sem isto o fim da requisição fecha a conexão do TestCase.
        request_started.disconnect(close_old_connections)
        request_finished.disconnect(close_old_connections)
        try:
            with self.assertLogs("django.request", "ERROR"):
                resposta = WSGIHandler()(environ, lambda codigo, cabecalhos, *_: status.append(codigo))
                b"".join(resposta)
                resposta.close()
        finally:
            request_started.connect(close_old_connections)
            request_finished.connect(close_old_connections)
        self.assertTrue(status[0].startswith("500"), status)

        eventos = self._eventos()
        self.assertEqual(len(eventos), 1)
        evento = eventos[0]
        self.assertEqual(evento["release"], "abc1234")
        self.assertEqual(evento["environment"], "production")
        self.assertEqual(evento["exception"]["values"][-1]["type"], "ErroDeTesteDoGlitchTip")

        texto = json.dumps(evento, ensure_ascii=False)
        for dado in (token, "cookie-de-sessao-secreto", SENHA, CNPJ, CNPJ_LIMPO, EMAIL,
                     "200.200.200.200", "su-glitchtip@test.local", "1234.56"):
            with self.subTest(dado=dado):
                self.assertNotIn(dado, texto)
        self.assertNotIn("user", evento)
        self.assertNotIn("data", evento["request"])
        self.assertTrue(evento["request"]["url"].endswith("/api/diagnostico/erro-de-teste"))
        self.assertEqual(evento["request"]["method"], "POST")
        self.assertNotIn("Authorization", evento["request"].get("headers", {}))
        self.assertNotIn("request", evento.get("_meta", {}))
        tipos = {item.type for envelope in self.transporte.envelopes for item in envelope.items}
        self.assertEqual(tipos, {"event"})
