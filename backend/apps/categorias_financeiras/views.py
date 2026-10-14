"""
As duas rotas das categorias financeiras.

    GET     /api/categorias-financeiras               público    — a lista do campo
    GET/PUT /api/categorias-financeiras/configuracao  diretoria  — a aba Categorias do /admin

Mesmo corte das rotas de `apps/departamentos/views.py`: a pública é anônima,
porque o consultor não faz login, e lê só o banco; a da diretoria usa a
permissão de publicar tabela, com 403 para gerente.

    { "versao": "<hash>", "sincronizado_em": "<ISO>|null",
      "modo": "lista"|"fixo",
      "categorias": [{"codigo", "descricao"}, ...] }
"""
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contas.permissions import PodePublicarTabela

from .servicos import (
    ConfiguracaoInvalida,
    estado_configuracao,
    estado_publico,
    salvar_configuracao,
)
from .throttling import CategoriasFinanceirasPublicoThrottle


def _sem_cache(resposta: Response) -> Response:
    # Uma lista cacheada na borda é a lista velha que estas rotas evitam.
    resposta["Cache-Control"] = "no-store"
    return resposta


class CategoriasFinanceirasView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CategoriasFinanceirasPublicoThrottle]

    def get(self, request):
        return _sem_cache(Response(estado_publico()))


class ConfiguracaoView(APIView):
    """Lista completa ou categoria fixa. Só diretoria, sem teto por IP."""

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def get(self, request):
        return _sem_cache(Response(estado_configuracao()))

    def put(self, request):
        try:
            salvar_configuracao(request.data, autor=request.user)
        except ConfiguracaoInvalida as erro:
            return _sem_cache(Response({"erro": str(erro)}, status=status.HTTP_400_BAD_REQUEST))
        return _sem_cache(Response({"ok": True, **estado_configuracao()}))
