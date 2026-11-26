"""
A pergunta ao Conecta (`apps.casais.conecta`), contra um Conecta falso no loopback.

O contrato é o da rota da TSK-1621: POST, o CNPJ no corpo e nunca na URL, a
chave da aplicação `lobby`, um `User-Agent` que a Cloudflare deixa passar e
nenhum redirecionamento seguido. E só o 200 do contrato vira sim ou não: o resto
é "sem resposta", com o motivo.
"""
import http.server
import json
import threading
import time

from django.test import SimpleTestCase, override_settings

from . import conecta

CHAVE = "AppKey lobby:segredo-de-teste"
CNPJ = "22122190000152"


class _Conecta(http.server.BaseHTTPRequestHandler):
    """Responde o que o teste mandou, e anota o que recebeu."""

    resposta = (200, {"cliente_ativo": True}, {})
    demora = 0
    recebidas = []

    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        type(self).recebidas.append({
            "metodo": self.command,
            "caminho": self.path,
            "cabecalhos": dict(self.headers),
            "corpo": corpo,
        })
        if type(self).demora:
            time.sleep(type(self).demora)
        status, dados, extras = type(self).resposta
        bruto = dados if isinstance(dados, bytes) else json.dumps(dados).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for nome, valor in extras.items():
            self.send_header(nome, valor)
        self.send_header("Content-Length", str(len(bruto)))
        self.end_headers()
        try:
            self.wfile.write(bruto)
        except BrokenPipeError:
            pass  # o lento: quem perguntou já desistiu

    do_GET = do_POST

    def log_message(self, *args):
        pass


class Base(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Conecta)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.servidor.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        super().tearDownClass()

    def setUp(self):
        _Conecta.resposta = (200, {"cliente_ativo": True}, {})
        _Conecta.demora = 0
        _Conecta.recebidas = []
        configuracao = override_settings(IDENTIDADE_URL=self.url, IDENTIDADE_APP_KEY=CHAVE)
        configuracao.enable()
        self.addCleanup(configuracao.disable)

    def responde(self, status, dados, **extras):
        _Conecta.resposta = (status, dados, extras)

    def assertSemResposta(self, motivo, tentar_em=None):
        with self.assertRaises(conecta.SemResposta) as erro:
            conecta.cliente_ativo(CNPJ)
        self.assertEqual(erro.exception.motivo, motivo)
        self.assertEqual(erro.exception.tentar_em, tentar_em)


class PedidoTest(Base):
    def test_o_pedido_e_o_do_contrato(self):
        self.assertIs(conecta.cliente_ativo(CNPJ), True)
        [pedido] = _Conecta.recebidas
        self.assertEqual(pedido["metodo"], "POST")
        # Sem barra no fim (com ela o Conecta dá 404) e sem o CNPJ na URL.
        self.assertEqual(pedido["caminho"], "/api/v1/clientes/ativo")
        self.assertEqual(json.loads(pedido["corpo"]), {"cnpj": CNPJ})
        cabecalhos = {k.lower(): v for k, v in pedido["cabecalhos"].items()}
        self.assertEqual(cabecalhos["authorization"], CHAVE)
        self.assertEqual(cabecalhos["content-type"], "application/json")
        # A Cloudflare barra o nome do urllib.
        self.assertNotIn("python-urllib", cabecalhos["user-agent"].lower())
        self.assertEqual(cabecalhos["user-agent"], conecta.USER_AGENT)

    def test_false_e_nao(self):
        self.responde(200, {"cliente_ativo": False})
        self.assertIs(conecta.cliente_ativo(CNPJ), False)

    def test_barra_no_fim_da_url_configurada_nao_vira_barra_dupla(self):
        with override_settings(IDENTIDADE_URL=self.url + "/"):
            conecta.cliente_ativo(CNPJ)
        self.assertEqual(_Conecta.recebidas[0]["caminho"], "/api/v1/clientes/ativo")

    def test_sem_chave_nao_pergunta(self):
        with override_settings(IDENTIDADE_APP_KEY=""):
            self.assertSemResposta("nao_configurado")
        self.assertEqual(_Conecta.recebidas, [])

    def test_url_sem_esquema_nao_pergunta(self):
        with override_settings(IDENTIDADE_URL="identidade-api"):
            self.assertSemResposta("nao_configurado")


class SemRespostaTest(Base):
    def test_redirecionamento_nao_e_seguido_e_a_chave_nao_sai(self):
        self.responde(302, {}, Location=self.url + "/outro-lugar")
        self.assertSemResposta("redirecionado")
        # Um pedido só: o 302 não foi seguido, nem para o mesmo destino.
        self.assertEqual([p["caminho"] for p in _Conecta.recebidas], ["/api/v1/clientes/ativo"])

    def test_429_traz_o_retry_after(self):
        self.responde(429, {"erro": "bloqueado_temporariamente"}, **{"Retry-After": "37"})
        self.assertSemResposta("limite", 37)

    def test_429_sem_retry_after(self):
        self.responde(429, {"erro": "bloqueado_temporariamente"})
        self.assertSemResposta("limite", None)

    def test_503_em_manutencao(self):
        self.responde(503, {"erro": "manutencao", "mensagem": "Volta já", "previsao": None})
        self.assertSemResposta("manutencao")

    def test_503_indisponivel(self):
        self.responde(503, {"erro": "indisponivel", "detalhe": "x"})
        self.assertSemResposta("indisponivel")

    def test_500(self):
        self.responde(500, b"<html>erro</html>")
        self.assertSemResposta("indisponivel")

    def test_chave_recusada(self):
        for status, codigo in ((401, "app_nao_autorizado"), (403, "sem_permissao")):
            with self.subTest(status=status):
                self.responde(status, {"erro": codigo})
                self.assertSemResposta("nao_autorizado")

    def test_400(self):
        self.responde(400, {"erro": "dados_invalidos", "detalhe": "cnpj"})
        self.assertSemResposta("dados_invalidos")

    def test_200_fora_do_contrato(self):
        for corpo in (b"<html>proxy</html>", {"cliente_ativo": "sim"}, {"ativo": True}, [True]):
            with self.subTest(corpo=corpo):
                self.responde(200, corpo)
                self.assertSemResposta("resposta_inesperada")

    def test_fora_do_ar(self):
        porta_fechada = http.server.HTTPServer(("127.0.0.1", 0), _Conecta)
        url = f"http://127.0.0.1:{porta_fechada.server_address[1]}"
        porta_fechada.server_close()
        with override_settings(IDENTIDADE_URL=url):
            self.assertSemResposta("indisponivel")

    def test_lento_demais_e_sem_resposta(self):
        _Conecta.demora = 1.5
        with override_settings(IDENTIDADE_TIMEOUT=1):
            self.assertSemResposta("indisponivel")
