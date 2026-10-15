"""
O Redis do cache `default` não pode derrubar a venda (TSK-1059).

Com `LOBBY_REDIS_URL` ligado, o cache `default` vira Redis — e é dele que o DRF
lê e grava o contador de TODO throttle, inclusive o das rotas anônimas do
consultor (`GET /api/catalogo`, `/api/departamentos`, o comprovante da venda).
O `RedisCache` do Django levanta exceção quando o Redis não responde, e o
throttle não a trata: um Redis reiniciando viraria 500 no lobby de todo
consultor, por causa de um CONTADOR.

Esta classe faz o contador falhar ABERTO: sem Redis, o `get` devolve o padrão e
o `set` não grava — a chamada passa sem teto naquele instante, e o log avisa.
É o mesmo que o Lobby fazia até aqui com o `LocMemCache` por processo (um teto
aproximado), e nunca um 500.

O `jti` da asserção do Conecta NÃO usa este cache: ele precisa falhar FECHADO
(sem Redis não há como saber se a asserção é repetida). Ele usa o alias
`assercao`, um `RedisCache` comum — ver `config/settings.py`.
"""
import logging
import time

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger("lobby.cache")

# Um aviso por minuto por processo: com o Redis fora, toda requisição passaria
# por aqui, e o log viraria só isto.
_INTERVALO_DO_AVISO = 60
_ultimo_aviso = {"quando": 0.0}


def _avisa(operacao, erro):
    agora = time.monotonic()
    if agora - _ultimo_aviso["quando"] >= _INTERVALO_DO_AVISO:
        _ultimo_aviso["quando"] = agora
        logger.warning(
            "Redis indisponível no cache default (%s: %s); os tetos de chamada "
            "ficam sem contar até ele voltar",
            operacao,
            type(erro).__name__,
        )


def _erros_do_redis():
    try:
        from redis.exceptions import RedisError
    except ImportError:  # sem o pacote, o RedisCache nem sobe
        return (OSError,)
    return (RedisError, OSError)


class RedisQueNaoDerrubaAVenda(RedisCache):
    """`RedisCache` cujo `get` e `set` falham aberto. Ver o cabeçalho."""

    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except _erros_do_redis() as erro:
            _avisa("get", erro)
            return default

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().set(key, value, timeout, version)
        except _erros_do_redis() as erro:
            _avisa("set", erro)
            return None
