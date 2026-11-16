"""
`GET /saude` — o healthcheck do compose (TSK-1513).

Até a TSK-1513 o healthcheck batia no `GET /api/catalogo`, que era anônimo. Com
o Lobby pedindo login, o catálogo responde 401 sem sessão, e o backend ficaria
`unhealthy` para sempre. Esta rota responde sem sessão e confere o que o
catálogo conferia de tabela: o Django de pé e o banco alcançável (pelo túnel).

Fica FORA de `/api/` de propósito, como o `/metrics`: o nginx do container só
encaminha `/api/`, `/django-admin/` e `/static/` ao backend, então ela não tem
caminho público (em lobby.sejaap.com.br/saude quem responde é o `index.html`).
Quem a alcança é o próprio container e o loopback da VPS, pela 8096.

Não diz nada além de "ok": nem versão, nem contagem, nem nome de banco.
"""
import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def saude(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as erro:
        # 503, e não a exceção subindo: o healthcheck roda a cada 30 s, e um
        # banco fora viraria um 500 (e um evento no GlitchTip) a cada rodada.
        # A venda de verdade, essa sim, leva o erro ao GlitchTip.
        logger.warning("healthcheck: banco indisponível (%s)", type(erro).__name__)
        resposta = JsonResponse({"ok": False}, status=503)
    else:
        resposta = JsonResponse({"ok": True})
    resposta["Cache-Control"] = "no-store"
    return resposta
