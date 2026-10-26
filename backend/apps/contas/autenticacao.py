"""
O token do Lobby, conferido de novo no login central (cliente 1.5, TSK-1238).

O access do Lobby dura horas (`LOBBY_JWT_MINUTOS`, 480 por padrão) e não há
rota de renovação: sem esta classe, quem fosse desativado no Conecta seguiria
autorizando negociação com o token já emitido até ele vencer. A
`revalidar_sessao` do cliente pergunta ao Conecta, no máximo a cada 5 minutos
por pessoa, se a identidade ainda vale neste app, e só uma resposta clara de
que não vale derruba a sessão. Conecta fora do ar ou em manutenção NUNCA
derruba (decisão do dono, 23/10/2026): quem já está autorizado segue.

É a receita JWT do docstring do `identidade_client.py`, sem mudança.
"""
from django.contrib.auth import logout
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

import identidade_client


class JWTRevalidado(JWTAuthentication):
    """`JWTAuthentication` que recusa o token de quem o Conecta desativou."""

    def get_user(self, validated_token):
        usuario = super().get_user(validated_token)
        if not identidade_client.revalidar_sessao(usuario):
            raise AuthenticationFailed("Sessão encerrada.", code="user_inactive")
        return usuario


def revalidar_sessao_middleware(get_response):
    """A mesma pergunta para a sessão do Django, a do /django-admin/.

    Logo depois do `AuthenticationMiddleware`. A API do Lobby não usa sessão
    (é JWT, conferido acima), então aqui só passa quem entrou no /django-admin/
    — a diretoria. Sem isto, a sessão de lá duraria o `SESSION_COOKIE_AGE`
    inteiro (duas semanas) depois de a pessoa ser desativada no Conecta.
    """

    def middleware(request):
        if (request.user.is_authenticated
                and not identidade_client.revalidar_sessao(request.user)):
            logout(request)
        return get_response(request)

    return middleware
