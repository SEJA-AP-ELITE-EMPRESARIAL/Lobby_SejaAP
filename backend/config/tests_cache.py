"""
O Redis do cache `default` não derruba a venda (TSK-1059, `config/cache.py`).

Com `LOBBY_REDIS_URL` ligado, os throttles das rotas do consultor passam a
contar no Redis. Se ele cair, o contador falha aberto e a rota responde; o `jti`
da asserção, no outro alias, continua falhando fechado.
"""
import time
from unittest import mock

from django.core.cache import caches
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

# Porta 1 do loopback: conexão recusada na hora, sem esperar o tempo de conexão.
REDIS_FORA = "redis://127.0.0.1:1/3"
OPCOES = {"socket_connect_timeout": 1, "socket_timeout": 1}


@override_settings(
    CACHES={
        "default": {"BACKEND": "config.cache.RedisQueNaoDerrubaAVenda", "LOCATION": REDIS_FORA, "OPTIONS": OPCOES},
        "assercao": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_FORA, "OPTIONS": OPCOES},
    }
)
class RedisForaTest(TestCase):
    def setUp(self):
        # O aviso sai uma vez por minuto por processo; cada teste começa como um
        # processo que ainda não avisou — e volta ao que estava no fim, para não
        # vazar para o teste seguinte.
        from config import cache

        antes = cache._ultimo_aviso["quando"]
        cache._ultimo_aviso["quando"] = None
        self.addCleanup(cache._ultimo_aviso.__setitem__, "quando", antes)

    def test_get_e_set_falham_aberto(self):
        cache = caches["default"]
        inicio = time.monotonic()
        self.assertEqual(cache.get("x", "padrão"), "padrão")
        self.assertIsNone(cache.set("x", 1, 10))
        self.assertLess(time.monotonic() - inicio, 3)

    def test_catalogo_do_consultor_responde_com_o_redis_fora(self):
        with self.assertLogs("lobby.cache", level="WARNING"):
            resposta = APIClient().get("/api/catalogo")
        self.assertEqual(resposta.status_code, 200)

    def test_o_aviso_sai_mesmo_com_a_maquina_ligada_ha_segundos(self):
        """O relógio do aviso é o `monotonic`, que conta desde o boot. Num runner
        de CI recém-ligado ele vale poucos segundos, e o primeiro aviso não pode
        depender disso."""
        with mock.patch("config.cache.time.monotonic", return_value=5.0):
            with self.assertLogs("lobby.cache", level="WARNING"):
                resposta = APIClient().get("/api/catalogo")
        self.assertEqual(resposta.status_code, 200)

    def test_um_aviso_por_minuto_e_nao_um_por_requisicao(self):
        with mock.patch("config.cache.time.monotonic", return_value=1000.0):
            with self.assertLogs("lobby.cache", level="WARNING") as logs:
                caches["default"].get("x")
                caches["default"].get("y")
                caches["default"].set("z", 1, 10)
        self.assertEqual(len(logs.output), 1)
        with mock.patch("config.cache.time.monotonic", return_value=1061.0):
            with self.assertLogs("lobby.cache", level="WARNING"):
                caches["default"].get("x")

    def test_alias_do_jti_continua_falhando_fechado(self):
        with self.assertRaises(Exception):
            caches["assercao"].add("jti", 1, 10)
