"""
IP real do visitante (TSK-195), testado pelo lado de quem tentaria forjá-lo.

Os headers aqui têm o formato que produção entrega ao Django (medido em
21/09/2026, ver `config/real_ip.py`):

- `REMOTE_ADDR` = o container do nginx;
- `CF-Connecting-IP` = o visitante, escrito pela Cloudflare;
- `X-Forwarded-For` = "<o que o cliente mandou>, <visitante>, <borda da
  Cloudflare>, <gateway do docker>".

A primeira posição do `X-Forwarded-For` é a que o atacante escreve. Os testes de
forja são os que importam: se um deles falhar, alguém voltou a deixar o cliente
escolher o próprio IP — e com ele o balde do throttle e o que a trilha grava.

Os testes de comportamento não importam `config.real_ip` no topo de propósito:
rodados contra o código anterior à correção, eles falham na asserção, e não num
ImportError que não provaria nada.
"""
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalogo.throttling import CatalogoPublicoThrottle
from apps.vendas.models import ComprovanteVenda
from apps.vendas.tests_comprovante import venda_de_tabela

# Visitantes com endereço público de verdade: o middleware recusa no
# `CF-Connecting-IP` o que não for global, e os TEST-NET (203.0.113.0/24,
# 198.51.100.0/24) não são globais para o `ipaddress`.
IP_VISITANTE = "177.10.20.45"
IP_OUTRO = "189.30.40.7"
IP_CONTAINER = "172.23.0.5"     # lobby-frontend na rede do compose
IP_GATEWAY = "172.23.0.1"       # o que o nginx do container anexa
IP_BORDA_CF = "172.71.238.240"  # uma borda da Cloudflare vista no access log
IP_PUBLICO = "8.8.8.8"          # quem fala direto com o gunicorn, de fora
IP_HEALTHCHECK = "127.0.0.1"    # o healthcheck do compose, que bate no catálogo

# O que a Cloudflare nunca escreve no `CF-Connecting-IP`: quem manda um destes
# contornou a Cloudflare e quer cair no balde de alguém de dentro.
IPS_DE_DENTRO = [
    IP_HEALTHCHECK,
    "::1",
    IP_CONTAINER,
    IP_GATEWAY,
    "10.0.0.1",
    "192.168.1.10",
    "169.254.169.254",
    "100.64.0.1",        # CGNAT
    "203.0.113.9",       # documentação
    "0.0.0.0",
    "fd00::1",
    "fe80::1",
    "::ffff:127.0.0.1",
]


def xff_de_producao(forjado, visitante=IP_VISITANTE):
    """O X-Forwarded-For como chega ao Django, com o texto do cliente na frente."""
    return f"{forjado}, {visitante}, {IP_BORDA_CF}, {IP_GATEWAY}"


def headers_de_producao(forjado="8.8.8.8", visitante=IP_VISITANTE):
    return {
        "REMOTE_ADDR": IP_CONTAINER,
        "HTTP_CF_CONNECTING_IP": visitante,
        "HTTP_X_FORWARDED_FOR": xff_de_producao(forjado, visitante),
        "HTTP_X_REAL_IP": IP_GATEWAY,
    }


def _processar(**meta):
    """Passa um request pelo middleware e devolve o request como ficou."""
    from config.real_ip import RealIPMiddleware

    request = RequestFactory().get("/", **meta)
    RealIPMiddleware(lambda r: r)(request)
    return request


class RealIPMiddlewareTest(SimpleTestCase):
    # ----- caminho de produção ------------------------------------------

    def test_usa_o_cf_connecting_ip_quando_vem_da_malha(self):
        request = _processar(**headers_de_producao())
        self.assertEqual(request.META["REMOTE_ADDR"], IP_VISITANTE)

    def test_x_forwarded_for_forjado_nao_muda_o_ip(self):
        request = _processar(**headers_de_producao(forjado="1.2.3.4"))
        self.assertEqual(request.META["REMOTE_ADDR"], IP_VISITANTE)

    def test_x_forwarded_for_passa_a_ter_so_o_ip_real(self):
        """É o que protege o `identidade_client.py`, que lê a primeira posição."""
        request = _processar(**headers_de_producao(forjado="1.2.3.4"))
        self.assertEqual(request.META["HTTP_X_FORWARDED_FOR"], IP_VISITANTE)

    def test_preserva_os_originais_para_depurar_a_cadeia(self):
        request = _processar(**headers_de_producao(forjado="1.2.3.4"))
        self.assertEqual(request.META["REMOTE_ADDR_ORIGINAL"], IP_CONTAINER)
        self.assertEqual(
            request.META["X_FORWARDED_FOR_ORIGINAL"], xff_de_producao("1.2.3.4")
        )
        # Sem o prefixo HTTP_: não pode voltar a ser lido como header.
        self.assertNotIn("HTTP_X_FORWARDED_FOR_ORIGINAL", request.META)

    def test_x_real_ip_nao_e_usado(self):
        """Em produção ele é o gateway do docker, o mesmo para todo mundo."""
        meta = headers_de_producao()
        del meta["HTTP_CF_CONNECTING_IP"]
        request = _processar(**meta)
        self.assertEqual(request.META["REMOTE_ADDR"], IP_CONTAINER)

    # ----- quem tenta escolher o próprio IP -----------------------------

    def test_conexao_publica_direta_nao_forja_o_ip(self):
        """Header de Cloudflare vindo de fora da malha é ignorado."""
        request = _processar(
            REMOTE_ADDR=IP_PUBLICO,
            HTTP_CF_CONNECTING_IP=IP_OUTRO,
            HTTP_X_FORWARDED_FOR=f"{IP_OUTRO}, 10.0.0.1",
        )
        self.assertEqual(request.META["REMOTE_ADDR"], IP_PUBLICO)
        self.assertEqual(request.META["HTTP_X_FORWARDED_FOR"], IP_PUBLICO)

    def test_header_com_lixo_nao_e_aceito(self):
        request = _processar(
            REMOTE_ADDR=IP_CONTAINER,
            HTTP_CF_CONNECTING_IP="nao-e-ip; DROP TABLE",
            HTTP_X_FORWARDED_FOR="nao-e-ip",
        )
        self.assertEqual(request.META["REMOTE_ADDR"], IP_CONTAINER)
        self.assertEqual(request.META["HTTP_X_FORWARDED_FOR"], IP_CONTAINER)

    def test_sem_header_nenhum_mantem_o_remote_addr(self):
        """O healthcheck bate direto no gunicorn, sem proxy nenhum."""
        request = _processar(REMOTE_ADDR="127.0.0.1")
        self.assertEqual(request.META["REMOTE_ADDR"], "127.0.0.1")

    def test_ipv6_da_cloudflare_e_aceito(self):
        request = _processar(
            REMOTE_ADDR=IP_CONTAINER, HTTP_CF_CONNECTING_IP="2804:14c:5b80::45"
        )
        self.assertEqual(request.META["REMOTE_ADDR"], "2804:14c:5b80::45")

    def test_pseudo_ipv4_da_cloudflare_e_aceito(self):
        """Com Pseudo IPv4 em "Overwrite headers", o visitante IPv6 chega assim.

        Recusá-lo juntaria todo visitante IPv6 no balde do container.
        """
        request = _processar(
            REMOTE_ADDR=IP_CONTAINER, HTTP_CF_CONNECTING_IP="240.16.0.1"
        )
        self.assertEqual(request.META["REMOTE_ADDR"], "240.16.0.1")

    def test_cf_connecting_ip_de_dentro_da_malha_e_recusado(self):
        """Loopback, privado e reservado no header: quem mandou contornou a
        Cloudflare. Aceitar poria o atacante no balde do healthcheck."""
        for de_dentro in IPS_DE_DENTRO:
            with self.subTest(cf=de_dentro):
                request = _processar(
                    REMOTE_ADDR=IP_CONTAINER,
                    HTTP_CF_CONNECTING_IP=de_dentro,
                    HTTP_X_FORWARDED_FOR=f"{de_dentro}, 8.8.8.8, {IP_GATEWAY}",
                )
                self.assertEqual(request.META["REMOTE_ADDR"], IP_CONTAINER)
                self.assertEqual(request.META["HTTP_X_FORWARDED_FOR"], IP_CONTAINER)

    # ----- o cliente copiado do Conecta ID ------------------------------

    def test_backend_identidade_le_o_ip_real(self):
        """`identidade_client.py` é cópia e lê o `X-Forwarded-For[0]`.

        Não se edita aqui (a fonte é o repositório do Conecta ID). O que o
        protege é o middleware reescrever o header antes dele.
        """
        from identidade_client import BackendIdentidade

        request = _processar(**headers_de_producao(forjado="1.2.3.4"))
        self.assertEqual(BackendIdentidade._ip(request), IP_VISITANTE)


class FiacaoTest(SimpleTestCase):
    """A classe existir não basta: precisa estar ligada, e na ordem certa."""

    def test_middleware_e_o_primeiro(self):
        self.assertEqual(settings.MIDDLEWARE[0], "config.real_ip.RealIPMiddleware")

    def test_drf_usa_o_remote_addr_normalizado(self):
        self.assertEqual(settings.REST_FRAMEWORK.get("NUM_PROXIES"), 0)


class ThrottleAnonimoTest(TestCase):
    """O balde do throttle anônimo é do visitante, não do texto do header."""

    def setUp(self):
        # O contador vive no cache do processo; o rollback do TestCase não o
        # desfaz.
        cache.clear()
        self.client = APIClient()

    def _catalogo(self, **meta):
        return self.client.get("/api/catalogo", **meta)

    def _com_teto(self, rate):
        # `THROTTLE_RATES` é atributo de classe, resolvido no import: trocar o
        # REST_FRAMEWORK por override_settings não chega nele (ver tests_senha).
        return patch.dict(
            CatalogoPublicoThrottle.THROTTLE_RATES, {"catalogo_publico": rate}
        )

    def test_trocar_o_x_forwarded_for_nao_reabre_o_balde(self):
        with self._com_teto("3/hour"):
            codigos = [
                self._catalogo(**headers_de_producao(forjado=f"10.9.8.{n}")).status_code
                for n in range(4)
            ]
        self.assertEqual(codigos, [200, 200, 200, 429])

    def test_cada_visitante_tem_o_seu_balde(self):
        with self._com_teto("2/hour"):
            for _ in range(2):
                self._catalogo(**headers_de_producao(visitante=IP_VISITANTE))
            bloqueado = self._catalogo(**headers_de_producao(visitante=IP_VISITANTE))
            outro = self._catalogo(**headers_de_producao(visitante=IP_OUTRO))
        self.assertEqual(bloqueado.status_code, 429)
        self.assertEqual(outro.status_code, 200)

    def test_cf_de_loopback_nao_enche_o_balde_do_healthcheck(self):
        """O healthcheck do compose faz GET /api/catalogo de 127.0.0.1, sem
        header. Quem contorna a Cloudflare e manda `CF-Connecting-IP: 127.0.0.1`
        não pode cair no balde dele: com o balde cheio, o healthcheck leva 429 e
        o `lobby-backend` vira "unhealthy"."""
        ataque = {"REMOTE_ADDR": IP_CONTAINER, "HTTP_CF_CONNECTING_IP": IP_HEALTHCHECK}
        with self._com_teto("3/hour"):
            codigos_ataque = [self._catalogo(**ataque).status_code for _ in range(3)]
            healthcheck = self._catalogo(REMOTE_ADDR=IP_HEALTHCHECK).status_code
        self.assertEqual(codigos_ataque, [200, 200, 200])
        self.assertEqual(healthcheck, 200)

    def test_chave_do_throttle_e_o_ip_real(self):
        with self._com_teto("5/hour"):
            self._catalogo(**headers_de_producao(forjado="8.8.8.8"))
        chaves = [
            CatalogoPublicoThrottle.cache_format % {"scope": "catalogo_publico", "ident": ip}
            for ip in (IP_VISITANTE, "8.8.8.8")
        ]
        self.assertIsNotNone(cache.get(chaves[0]), "o balde tem que ser o do visitante")
        self.assertIsNone(cache.get(chaves[1]), "o IP forjado não pode virar balde")


@override_settings(AUTH_CENTRAL_ATIVO=True)
class IpAtribuidoTest(TestCase):
    """O IP que sai do app — para o Conecta ID e para o comprovante — é o real."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_login_manda_ao_conecta_id_o_ip_real(self):
        with patch("apps.contas.views.ClienteIdentidade") as Cliente:
            # Falha de credencial basta: o que se confere é o IP da chamada.
            from identidade_client import CredencialInvalida

            Cliente.return_value.verificar.side_effect = CredencialInvalida("x")
            self.client.post(
                "/api/sessao",
                {"email": "fulano@sejaap.com.br", "senha": "x"},
                format="json",
                **headers_de_producao(forjado="8.8.8.8"),
            )
        _, kwargs = Cliente.return_value.verificar.call_args
        self.assertEqual(kwargs["ip"], IP_VISITANTE)

    def test_comprovante_grava_o_ip_real(self):
        resposta = self.client.post(
            "/api/venda/comprovante",
            venda_de_tabela(),
            format="json",
            **headers_de_producao(forjado="8.8.8.8"),
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(
            ComprovanteVenda.objects.get().ip_emissao, IP_VISITANTE
        )

    def test_comprovante_com_x_forwarded_for_lixo_nao_quebra(self):
        """Antes, o texto do header ia cru para uma coluna de IP."""
        meta = headers_de_producao()
        meta["HTTP_X_FORWARDED_FOR"] = "nao-e-um-ip"
        resposta = self.client.post(
            "/api/venda/comprovante", venda_de_tabela(), format="json", **meta
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(ComprovanteVenda.objects.get().ip_emissao, IP_VISITANTE)
