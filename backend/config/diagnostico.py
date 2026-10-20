"""
`POST /api/diagnostico/erro-de-teste` — levanta uma exceção de propósito (TSK-1142).

Serve para conferir, depois de um deploy, que o erro chega ao GlitchTip (projeto
`lobby`) com o release igual ao commit da imagem: a rota termina em 500 e o SDK
manda o evento. Sem SENTRY_DSN, só devolve o 500. O roteiro está no
deploy/RUNBOOK.md, "GlitchTip".

Só superusuário do Django (`is_superuser`), que nenhum fluxo do Lobby dá: a
diretoria é só `is_staff`. Qualquer outra conta leva 403 sem a
exceção acontecer, para ninguém mais conseguir encher o GlitchTip de issues
falsas. POST, e não GET, para um link ou um <img> não dispararem a rota por
engano. Teto de 10 por hora por pessoa. A asserção do Conecta não chega aqui: a
rota não está na lista fechada (`ROTAS_LIBERADAS`).
"""
from rest_framework.permissions import BasePermission
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView


class ErroDeTesteDoGlitchTip(RuntimeError):
    """A exceção da rota. O nome próprio facilita achar a issue no GlitchTip."""


class SoSuperusuario(BasePermission):
    message = "Somente superusuário dispara o erro de teste."

    def has_permission(self, request, view):
        usuario = request.user
        return bool(usuario and usuario.is_authenticated and usuario.is_superuser)


class DiagnosticoThrottle(UserRateThrottle):
    scope = "diagnostico"


class ErroDeTesteView(APIView):
    # A autenticação é a padrão (o JWT do `POST /api/sessao`). A permissão vem
    # antes do teto no DRF: quem leva 403 não gasta a cota de ninguém.
    permission_classes = [SoSuperusuario]
    throttle_classes = [DiagnosticoThrottle]

    def post(self, request):
        raise ErroDeTesteDoGlitchTip(
            "Erro de teste do GlitchTip (TSK-1142), disparado por superusuário."
        )
