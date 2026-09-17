"""
As duas rotas dos departamentos.

    GET     /api/departamentos               público    — a lista do dropdown
    GET/PUT /api/departamentos/configuracao  diretoria  — a aba Departamentos do /admin

A PÚBLICA

Pública pelo mesmo motivo do catálogo: o consultor não faz login. Lê o banco,
nunca o Omie (ver `models.py`), então responde na mesma velocidade com o Omie de
pé ou fora do ar.

    { "versao": "<hash>", "sincronizado_em": "<ISO>|null",
      "modo": "lista"|"fixo",
      "departamentos": [{"codigo", "descricao", "estrutura"}, ...] }

Só os ATIVOS, e só o fixo quando a diretoria fixou um (ver `servicos.py`). O front
compara `versao` com a que já tem para saber se algo mudou desde que a página
abriu.

A DA DIRETORIA

Mesmo corte do `/api/cobranca`: configurar o departamento vale para todas as
próximas vendas, alcance de tabela, e não de uma venda. Gerente recebe 403; 401
fica só para "não autenticado", porque é o que derruba a sessão do `/admin`.
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
from .throttling import DepartamentosPublicoThrottle


def _sem_cache(resposta: Response) -> Response:
    # Atrás da Cloudflare, uma lista cacheada na borda é justamente a lista
    # velha que estas rotas existem para evitar.
    resposta["Cache-Control"] = "no-store"
    return resposta


class DepartamentosView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [DepartamentosPublicoThrottle]

    def get(self, request):
        return _sem_cache(Response(estado_publico()))


class ConfiguracaoView(APIView):
    """Lista completa ou departamento fixo. Só diretoria, sem teto por IP."""

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def get(self, request):
        return _sem_cache(Response(estado_configuracao()))

    def put(self, request):
        try:
            salvar_configuracao(request.data, autor=request.user)
        except ConfiguracaoInvalida as erro:
            return _sem_cache(Response({"erro": str(erro)}, status=status.HTTP_400_BAD_REQUEST))
        return _sem_cache(Response({"ok": True, **estado_configuracao()}))
