"""
O Redis do cache `default` não derruba a venda (TSK-1059, `config/cache.py`).

Com `LOBBY_REDIS_URL` ligado, os throttles das rotas do consultor passam a
contar no Redis. Se ele cair, o contador falha aberto e a rota responde; o `jti`
da asserção, no outro alias, continua falhando fechado.
"""
import time

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
        # O aviso sai uma vez por minuto por processo; cada teste começa do zero.
        from config import cache

        cache._ultimo_aviso["quando"] = 0.0

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

    def test_alias_do_jti_continua_falhando_fechado(self):
        with self.assertRaises(Exception):
            caches["assercao"].add("jti", 1, 10)
