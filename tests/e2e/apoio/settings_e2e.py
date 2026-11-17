"""
Settings do backend no e2e do sinal de presença (TSK-1520): os de sempre, isolados.

O `config/settings.py` lê o `backend/.env`, que numa máquina de desenvolvimento
pode apontar para o Postgres e o Conecta de PRODUÇÃO e trazer o DSN do
GlitchTip. O `load_dotenv` não sobrescreve o que já está no ambiente, então tudo
que importa é fixado AQUI, antes de importar o settings: banco SQLite numa pasta
descartável, sem Redis (cache local), sem n8n, sem Omie, sem GlitchTip, sem a
integração do Conecta, e o login central apontado para o Conecta falso em
127.0.0.1. O `rodar-presenca.sh` é quem passa `E2E_BANCO` e `E2E_CONECTA`.
"""
import os

_conecta = os.environ["E2E_CONECTA"]
if not _conecta.startswith("http://127.0.0.1:"):
    raise SystemExit(f"E2E_CONECTA tem de ser o Conecta falso em 127.0.0.1, não {_conecta!r}")

os.environ.update(
    DATABASE_URL="",
    LOBBY_DB_NAME="",
    DB_NAME="",
    LOBBY_REDIS_URL="",
    LOBBY_N8N_TOKEN="",
    LOBBY_METRICS_TOKEN="",
    OMIE_APP_KEY="",
    OMIE_APP_SECRET="",
    CONECTA_ASSERCAO_CHAVES="",
    SENTRY_DSN="",
    DJANGO_DEBUG="true",
    DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost",
    SECURE_SSL_REDIRECT="false",
    AUTH_CENTRAL_ATIVO="true",
    IDENTIDADE_URL=_conecta,
    IDENTIDADE_APP_KEY="AppKey lobby:chave-do-e2e",
    IDENTIDADE_TIMEOUT="2",
)

from config.settings import *  # noqa: E402,F401,F403

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["E2E_BANCO"]}
}
