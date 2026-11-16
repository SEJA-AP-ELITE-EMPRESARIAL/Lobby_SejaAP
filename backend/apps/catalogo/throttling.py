"""
Limite da leitura do catálogo.

O `GET /api/catalogo` é chamado por todo consultor a cada abertura do lobby.
Até a TSK-1513 ele era anônimo e o teto contava por IP; desde então exige
sessão, e o teto é por pessoa (`PorPessoaThrottle`). O nome da classe e o
`scope` (`catalogo_publico`, `LOBBY_RATE_CATALOGO`) ficaram os de antes para
não mexer no `lobby.env` de produção.

Vale o aviso que vem da casa: sem `LOBBY_REDIS_URL` o DRF usa o cache `default`
local (`LocMemCache`), que é POR PROCESSO. Com gunicorn em 3 workers o teto real
é ~3× o configurado, e zera a cada deploy — ver `config/settings.py`, bloco
CACHES.
"""
from apps.contas.throttling import PorPessoaThrottle


class CatalogoPublicoThrottle(PorPessoaThrottle):
    """Leitura do catálogo. Teto folgado: é a primeira chamada de cada venda."""

    scope = "catalogo_publico"
