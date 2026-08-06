"""
Limite do login.

O `POST /api/sessao` é anônimo e cada tentativa custa um Argon2 no Conecta ID.
Duas razões para haver teto aqui, além do que o serviço central já faz:

1. O throttle do Conecta ID para verificação é 60/min **para o app inteiro**
   (`conecta-id/config/settings.py:203-212`), não por pessoa. Um script batendo
   no Lobby consumiria a cota e derrubaria o login de quem está vendendo.
2. O bloqueio por IP de lá depende do Redis estar configurado. Se não estiver,
   ele é ficção — e este teto passa a ser a única contenção real.

Vale o mesmo aviso de `apps/catalogo/throttling.py`: sem `LOBBY_REDIS_URL`, o
contador é por processo do gunicorn.
"""
from rest_framework.throttling import AnonRateThrottle


class LoginThrottle(AnonRateThrottle):
    scope = "login"
