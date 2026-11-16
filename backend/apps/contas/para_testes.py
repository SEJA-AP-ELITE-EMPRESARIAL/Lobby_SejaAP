"""
Ajudas da suíte para a sessão de quem vende (TSK-1513).

Desde a TSK-1513 o catálogo, as listas e o comprovante pedem a sessão do Lobby.
Os testes que liam essas rotas como o consultor anônimo de antes passam a ler
como um consultor logado SEM papel — o caso comum, e o de quem acabou de entrar
pela primeira vez.

Fora do padrão `test*.py` de propósito: não tem teste, só ajuda.
"""
import uuid

from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .models import VinculoIdentidade

EMAIL_DO_CONSULTOR = "consultor@sejaap.com.br"


def consultor(email=EMAIL_DO_CONSULTOR, papel=""):
    """A conta local de quem vende, com o vínculo, como nasce no primeiro login.

    Reaproveita a conta se o e-mail já existe: vários testes chamam isto mais de
    uma vez.
    """
    usuario, criado = User.objects.get_or_create(username=email, defaults={"email": email})
    if criado:
        usuario.set_unusable_password()
        usuario.save()
        VinculoIdentidade.objects.create(usuario=usuario, identidade_id=uuid.uuid4(), papel=papel)
    return usuario


def cliente_logado(usuario=None, **cabecalhos):
    """`APIClient` com a sessão de `usuario` (o consultor sem papel, se None).

    Pula a camada do JWT (`force_authenticate`), como os testes de escrita já
    fazem. Quem precisa do token de verdade usa `token_de`.
    """
    cliente = APIClient(**cabecalhos)
    cliente.force_authenticate(user=usuario or consultor())
    return cliente


def token_de(usuario):
    """O token de sessão que o `POST /api/sessao` emitiria para `usuario`."""
    return str(RefreshToken.for_user(usuario).access_token)


def emite_comprovante(dados, autorizador=None, vendedor=None, **cabecalhos):
    """`POST /api/venda/comprovante` como o lobby faz desde a TSK-1513.

    A sessão de quem vende (`vendedor`, o consultor sem papel se None) e, se
    houver, o token de quem AUTORIZA no `X-Lobby-Autorizacao` — de verdade, e
    não por `force_authenticate`: é ele que destrava a venda negociada.
    """
    if autorizador is not None:
        cabecalhos.setdefault("HTTP_X_LOBBY_AUTORIZACAO", f"Bearer {token_de(autorizador)}")
    return cliente_logado(vendedor).post("/api/venda/comprovante", dados, format="json", **cabecalhos)
