"""
Limites das rotas anônimas.

O `GET /api/catalogo` é chamado por todo consultor a cada abertura do lobby e
não pede credencial nenhuma — é público por desenho, não por esquecimento.
Sem teto, é também o jeito mais barato de derrubar a cotação de todo mundo.

Vale o aviso que vem da casa: nem o CRM nem o Formulários definem `CACHES`, e o
DRF usa o cache `default` (`LocMemCache`), que é POR PROCESSO. Com gunicorn em
3 workers o teto real é ~3× o configurado, e zera a cada deploy. Enquanto não
houver cache compartilhado, trate estes números como contenção de rajada, não
como limite exato — ver `config/settings.py`, bloco CACHES.
"""
from rest_framework.throttling import AnonRateThrottle


class CatalogoPublicoThrottle(AnonRateThrottle):
    """Leitura do catálogo. Teto folgado: é a primeira chamada de cada venda."""

    scope = "catalogo_publico"
