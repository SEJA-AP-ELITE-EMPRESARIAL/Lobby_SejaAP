"""
Configuração do Django para o backend do Lobby Seja AP.

Espelha a stack da casa (`formulario-financeiro`, que por sua vez espelha o
`crm-funil-resgate`): DRF + SimpleJWT + WhiteNoise, banco na db-sejaap por
túnel SSH, login pelo Conecta ID.

DUAS COISAS ESPECÍFICAS DESTE APP, que o diferenciam dos irmãos:

1. **O Lobby é anônimo por natureza.** Qualquer consultor abre, cota, cadastra e
   envia a venda sem login. O que exige credencial é NEGOCIAR — alterar valor de
   produto ou de parcela — e publicar a tabela de preços. Nunca acrescente
   `LoginRequiredMiddleware`, `LOGIN_URL` forçado ou `login_required` nas rotas
   do fluxo de venda: o app inteiro cairia atrás de um portão que não deve
   existir.

2. **Sem CORS, de propósito.** O `index.html` chama `/api/catalogo` por URL
   relativa. Front e API são servidos na mesma origem pelo nginx do host. Se um
   dia isso mudar, o certo é o proxy, não `django-cors-headers` — a alternativa
   é abrir a escrita da tabela de preços para outra origem.

Config por variáveis de ambiente (.env). O default roda com SQLite, zero setup.
"""
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import os
import sys

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(nome: str, padrao: bool = False) -> bool:
    return os.environ.get(nome, str(padrao)).strip().lower() in {"1", "true", "yes", "on"}


def _env_lista(nome: str, padrao: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(nome, padrao).split(",") if item.strip()]


# === Núcleo ===
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-inseguro-troque-em-producao")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = _env_lista("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceiros
    "rest_framework",
    # Apps do projeto
    "apps.catalogo",
    "apps.contas",
    "apps.vendas",
    "apps.departamentos",
    "apps.categorias_financeiras",
    "apps.dh_avaliacao",
    "apps.integracao",
]

MIDDLEWARE = [
    # PRIMEIRO, sempre: troca o IP do container pelo do visitante
    # (CF-Connecting-IP) antes que qualquer outro componente leia REMOTE_ADDR.
    # Ver config/real_ip.py.
    "config.real_ip.RealIPMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serve os estáticos do /django-admin/ em produção.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # O cabeçalho da asserção do Conecta só vale nas views que a declaram; em
    # qualquer outra, 403 ROTA_NAO_LIBERADA (TSK-1059). Ver
    # apps/contas/autenticacao_conecta.py.
    "apps.contas.autenticacao_conecta.FechaRotasForaDaLista",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# === Banco ===
# Cascata: DATABASE_URL > LOBBY_DB_* > SQLite local.
# Em produção é o Postgres `lobby` na db-sejaap (179.197.237.95), alcançado pelo
# container `db-tunnel` — a porta não é exposta na internet.
def _banco_da_url(url: str) -> dict | None:
    if not url:
        return None
    partes = urlparse(url)
    if partes.scheme not in {"postgres", "postgresql"}:
        return None
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": partes.path.lstrip("/"),
        "USER": unquote(partes.username or ""),
        "PASSWORD": unquote(partes.password or ""),
        "HOST": partes.hostname or "",
        "PORT": str(partes.port or ""),
        "OPTIONS": {"sslmode": os.environ.get("DB_SSLMODE", "require")},
        "CONN_MAX_AGE": 60,
    }


_banco = _banco_da_url(os.environ.get("DATABASE_URL", ""))
if _banco is None and os.environ.get("LOBBY_DB_NAME"):
    _banco = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["LOBBY_DB_NAME"],
        "USER": os.environ.get("LOBBY_DB_USER", ""),
        "PASSWORD": os.environ.get("LOBBY_DB_PASSWORD", ""),
        "HOST": os.environ.get("LOBBY_DB_HOST", "db-tunnel"),
        "PORT": os.environ.get("LOBBY_DB_PORT", "5437"),
        "OPTIONS": {"sslmode": os.environ.get("DB_SSLMODE", "require")},
        "CONN_MAX_AGE": 60,
    }
if _banco is None:
    _banco = {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}

DATABASES = {"default": _banco}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# === Cache ===
# Importa mais do que parece: é daqui que o DRF tira o contador de throttle da
# rota pública. `LocMemCache` é POR PROCESSO — com gunicorn em 3 workers, o teto
# real fica ~3× o configurado e zera a cada deploy.
#
# Desde a TSK-1059 o cache compartilhado deixou de ser opcional para uma coisa:
# a asserção do Conecta guarda nele o `jti` contra repetição (ADR-002 §4.6), e
# sem Redis a integração fica DESLIGADA em produção (503 INTEGRACAO_DESLIGADA).
# Em produção `LOBBY_REDIS_URL` aponta para o Redis PRÓPRIO do Lobby (TSK-1068),
# o serviço `redis` do docker-compose.yml, com senha e só na rede interna do
# compose — ver o .env.example e o deploy/RUNBOOK.md.
#
# DOIS ALIASES PARA O MESMO REDIS, de propósito:
#   default   os throttles. Falha ABERTA (`config/cache.py`): Redis fora não
#             pode virar 500 no lobby do consultor por causa de um contador.
#   assercao  o `jti`. Falha FECHADA: sem Redis não há como saber se a asserção
#             é repetida, e a integração responde 503.
# Tempo curto de conexão nos dois: um Redis que não responde não pode segurar a
# requisição do consultor por segundos.
_redis = os.environ.get("LOBBY_REDIS_URL", "").strip()
_opcoes_do_redis = {"socket_connect_timeout": 1, "socket_timeout": 1}
CACHES = (
    {
        "default": {
            "BACKEND": "config.cache.RedisQueNaoDerrubaAVenda",
            "LOCATION": _redis,
            "KEY_PREFIX": "lobby",
            "OPTIONS": _opcoes_do_redis,
        },
        "assercao": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis,
            "KEY_PREFIX": "lobby",
            "OPTIONS": _opcoes_do_redis,
        },
    }
    if _redis
    else {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "assercao": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "assercao",
        },
    }
)


# === Autenticação ===
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# === Conecta ID ===
# Quem guarda senha é o Conecta ID; este app não tem senha local nenhuma.
# O papel (gerente x diretoria) é daqui — o serviço central não guarda papel de
# negócio de propósito.
AUTH_CENTRAL_ATIVO = _env_bool("AUTH_CENTRAL_ATIVO", False)
IDENTIDADE_URL = os.environ.get("IDENTIDADE_URL", "http://identidade-api:8000")
IDENTIDADE_APP_KEY = os.environ.get("IDENTIDADE_APP_KEY", "")
IDENTIDADE_TIMEOUT = int(os.environ.get("IDENTIDADE_TIMEOUT", "5"))
IDENTIDADE_RESOLVER_USUARIO = "apps.contas.identidade.resolver_usuario"

# === Asserção do Conecta (TSK-1059, ADR-002 §13) ===
# O Conecta administra o Lobby chamando estas rotas em nome de uma pessoa, com um
# JWT curto assinado em Ed25519 no cabeçalho `X-Conecta-Assercao`. Aqui só mora a
# CHAVE PÚBLICA: a privada nunca sai do servidor do Conecta.
#
# `CONECTA_ASSERCAO_CHAVES` é um conjunto JWK (`{"keys": [...]}`), por `kid`. É
# conjunto, e não uma chave só, para a troca de chave não ter janela (§8.2).
# Vazio = integração DESLIGADA: as rotas dela respondem 503 INTEGRACAO_DESLIGADA
# e o resto do Lobby (o /admin, o login) segue igual. Ver
# `apps/contas/autenticacao_conecta.py`.
#
# A audiência NÃO é configurável, de propósito: é sempre `lobby`. Uma variável
# aqui só serviria para alguém, por engano, fazer o Lobby aceitar a asserção que
# o Conecta assinou para o Kanban ou para o Formulário.
CONECTA_ASSERCAO_CHAVES = os.environ.get("CONECTA_ASSERCAO_CHAVES", "").strip()
CONECTA_ASSERCAO_EMISSOR = os.environ.get("CONECTA_ASSERCAO_EMISSOR", "conecta").strip()
# 10 s nas duas pontas (§4.5). Teto de 30 no código: recusa por relógio se
# resolve no NTP, e não alargando a janela em que uma asserção capturada serve.
CONECTA_ASSERCAO_TOLERANCIA_SEGUNDOS = max(
    0, min(30, int(os.environ.get("CONECTA_ASSERCAO_TOLERANCIA_SEGUNDOS", "10")))
)

# === n8n ===
# Segredo compartilhado com o fluxo do n8n, que o usa para chamar
# /api/venda/validar. Sem ele o endpoint responde 503 e o n8n cai no ramo de
# conferência manual — falha visível, não silenciosa.
LOBBY_N8N_TOKEN = os.environ.get("LOBBY_N8N_TOKEN", "")

# === Omie ===
# Só para listar os departamentos do Elite e da APN (`apps/departamentos/omie.py`),
# de hora em hora, pelo container `lobby-departamentos`. A chave é do ERP inteiro
# — lê e escreve o financeiro —, então vive só no .env do servidor. Sem ela, cada
# rodada loga a falha e o dropdown fica com a última lista que está no banco.
OMIE_APP_KEY = os.environ.get("OMIE_APP_KEY", "")
OMIE_APP_SECRET = os.environ.get("OMIE_APP_SECRET", "")
OMIE_TIMEOUT = int(os.environ.get("OMIE_TIMEOUT", "20"))
# Categorias financeiras da APN (TSK-879): a mesma chave, o `ListarCategorias`.
# Só entram as categorias sob estes prefixos do plano de contas, separados por
# vírgula. "1.10" é o grupo "APN" do Omie, com uma categoria por turma. Ver
# `apps/categorias_financeiras/fonte.py`.
CATEGORIAS_FINANCEIRAS_PREFIXOS = os.environ.get("CATEGORIAS_FINANCEIRAS_PREFIXOS", "1.10")

# === Prometheus ===
# Token do /metrics. Vazio = endpoint desabilitado (404), que é o padrão certo:
# um /metrics aberto entrega o volume de vendas da empresa e quantas pessoas
# podem autorizar desconto. Mesmo desenho do Conecta ID.
LOBBY_METRICS_TOKEN = os.environ.get("LOBBY_METRICS_TOKEN", "")

# Só o backend central. Sem `ModelBackend`: uma senha local seria uma segunda
# porta para a mesma conta, fora da política do Conecta ID.
#
# DUAS CONSEQUÊNCIAS QUE PEGAM DESPREVENIDO:
# 1. O /django-admin/ passa a pedir o E-MAIL no campo "Usuário".
# 2. `AUTH_CENTRAL_ATIVO=False` NÃO é rollback — é tranca. Sem ele ninguém
#    entra, nem superusuário, porque não há senha local para cair de volta.
#    Para a primeira pessoa da diretoria, use `manage.py promover_no_lobby`.
AUTHENTICATION_BACKENDS = ["identidade_client.BackendIdentidade"]


# === DRF ===
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Fechado por padrão. O que é público abre explicitamente com AllowAny —
    # hoje, só o GET do catálogo.
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # Todo erro sai como {"erro": "..."}, que é o que o front lê. Sem isto, o
    # 403 de permissão chegaria como {"detail": ...} e viraria "Falha ao salvar"
    # na tela, escondendo o motivo real.
    "EXCEPTION_HANDLER": "config.erros.tratar_excecao",
    # Os throttles anônimos abaixo contam por IP, e o IP que vale é o
    # REMOTE_ADDR que o `RealIPMiddleware` já trocou pelo do visitante. Sem esta
    # linha o DRF usa o X-Forwarded-For INTEIRO como chave do balde, e trocar o
    # header a cada requisição zerava o limite (TSK-195). Zero, e não o número
    # de saltos: contar saltos quebra em silêncio quando a topologia muda.
    "NUM_PROXIES": 0,
    "DEFAULT_THROTTLE_RATES": {
        # Leitura do catálogo: é a primeira chamada de toda venda, e o consultor
        # em campo recarrega a página com frequência.
        "catalogo_publico": os.environ.get("LOBBY_RATE_CATALOGO", "240/hour"),
        # Login. O número precisa conviver com uma característica do produto:
        # a autorização de negociação é POR REGISTRO, não sessão longa — o
        # gerente digita a credencial dele a cada venda que destrava. Dez vendas
        # numa tarde são dez logins, e vários gerentes atrás do mesmo IP do
        # escritório somam rápido. Um teto de 20/hora trancaria o time no meio
        # do expediente.
        #
        # O que protege de força bruta mesmo não é este número: é o bloqueio por
        # conta e por IP do Conecta ID, mais a cota de 60/min por app de lá.
        # Este teto é contenção de rajada.
        "login": os.environ.get("LOBBY_RATE_LOGIN", "120/hour"),
        # Emissão de comprovante de venda. Folgado: uma venda pode ser
        # reenviada algumas vezes. Existe para o endpoint não virar oráculo
        # de "qual valor passa na conferência".
        "comprovante": os.environ.get("LOBBY_RATE_COMPROVANTE", "120/hour"),
        # Definição de senha por link. Baixo de propósito: o caminho normal é
        # uma pessoa usando um link uma vez. Sobra folga para quem erra a
        # política de senha algumas vezes seguidas e tenta de novo.
        "definir_senha": os.environ.get("LOBBY_RATE_DEFINIR_SENHA", "20/hour"),
        # Lista de departamentos. O front reconsulta a cada 5 minutos, ao voltar
        # para a aba e antes de enviar — e vários consultores dividem o IP do
        # escritório. Uma resposta de 10 linhas lidas do banco, sem Omie no meio.
        "departamentos_publico": os.environ.get("LOBBY_RATE_DEPARTAMENTOS", "600/hour"),
        # Categorias financeiras da APN (TSK-877): mesmo ritmo dos departamentos.
        "categorias_financeiras_publico": os.environ.get("LOBBY_RATE_CATEGORIAS_FINANCEIRAS", "600/hour"),
        # Valores das modalidades do DH - Avaliação (TSK-1056): o lobby os
        # reconsulta no ritmo dos departamentos.
        "dh_avaliacao_publico": os.environ.get("LOBBY_RATE_DH_AVALIACAO", "600/hour"),
        # Integração com o Conecta (TSK-1059, ADR-002 §10.4). Toda chamada sai do
        # MESMO IP, o do servidor do Conecta: teto por IP limitaria o portal
        # inteiro de uma vez. Depois da asserção a chamada tem pessoa, e o teto é
        # por pessoa. As recusas de asserção (antes de haver pessoa) contam por
        # IP — e vindo do IP do Conecta são defeito, não tráfego.
        "integracao_leitura": os.environ.get("LOBBY_RATE_INTEGRACAO_LEITURA", "120/min"),
        "integracao_escrita": os.environ.get("LOBBY_RATE_INTEGRACAO_ESCRITA", "30/min"),
        "integracao_falhas": os.environ.get("LOBBY_RATE_INTEGRACAO_FALHAS", "60/min"),
    },
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=int(os.environ.get("LOBBY_JWT_MINUTOS", "480"))
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "AUTH_HEADER_TYPES": ("Bearer",),
}


# === Internacionalização ===
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# === Estáticos ===
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
if not DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        },
    }


# === Segurança ===
# Atrás da Cloudflare + nginx do host: quem termina o TLS é a borda, e o gunicorn
# recebe HTTP em texto claro na rede do docker.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", not DEBUG)

# Rodando `manage.py test`, o redirect fica desligado. O cliente de teste do
# Django fala HTTP puro e não passa pelo nginx, então TODA requisição viraria um
# 301 para https e a suíte inteira falharia com "301 != 200".
#
# Isso não é conveniência: sem esta linha, `docker compose exec backend
# manage.py test` não funciona em produção — e é exatamente durante um incidente
# que se quer poder rodar a suíte contra o banco de verdade.
if "test" in sys.argv:
    SECURE_SSL_REDIRECT = False

# O /metrics fica FORA do redirect para https, e isso é necessário, não frouxidão.
#
# O Prometheus bate direto no gunicorn pela 8096 do loopback, sem passar pelo
# nginx — então ele não tem quem ponha o X-Forwarded-Proto, e toda coleta
# levaria um 301 para https numa porta que não fala TLS. O alvo ficaria DOWN
# para sempre, e o alerta de "Lobby fora do ar" tocaria com o Lobby de pé.
#
# O que se abre com isso é uma rota em texto claro no LOOPBACK da VPS, fechada
# por token. Quem está no loopback da .164 é root — não há o que interceptar.
SECURE_REDIRECT_EXEMPT = [r"^metrics$"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_TRUSTED_ORIGINS = _env_lista("DJANGO_CSRF_TRUSTED_ORIGINS")
X_FRAME_OPTIONS = "DENY"
# Censura o cabeçalho da asserção do Conecta no relatório de erro (ADR-002
# §10.2): o filtro padrão do Django não o reconhece como segredo.
DEFAULT_EXCEPTION_REPORTER_FILTER = "config.erros.FiltroDeRelatorio"

# HSTS desligado por padrão, e isso é decisão, não esquecimento: quem termina o
# TLS é a Cloudflare, que já tem HSTS próprio no painel — ligar nos dois lados
# só duplica a responsabilidade. E ligar aqui é caro de desfazer: o navegador
# guarda a política pelo tempo declarado, então um erro de certificado no meio
# do caminho tranca o domínio pelo mesmo período.
# Se um dia for para ligar, ligue na borda primeiro e observe.
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)


# === Log ===
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simples": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simples"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOBBY_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
