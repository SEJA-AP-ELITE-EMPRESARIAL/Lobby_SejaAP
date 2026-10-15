"""
As duas rotas dos valores do DH - Avaliação.

    GET     /api/dh-avaliacao/valores               público    — o que o lobby mostra
    GET/PUT /api/dh-avaliacao/valores/configuracao  diretoria  — a aba DH - Avaliação do /admin

A PÚBLICA

Pública pelo mesmo motivo do catálogo: o consultor não faz login.

    { "versao": "<hash>",
      "modalidades": [{"id": "relatorio", "nome": "Relatório", "valor": 150, "fixo": false},
                      {"id": "devolutiva", "nome": "Devolutiva", "valor": 450, "fixo": false}] }

A DA DIRETORIA

Mesmo corte do `/api/departamentos/configuracao`: o que se grava vale para todas
as próximas vendas, alcance de tabela. Gerente recebe 403; 401 fica só para "não
autenticado", porque é o que derruba a sessão do `/admin`.
"""
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contas.autenticacao_conecta import AceitaAssercao, recusa
from apps.contas.permissions import PodePublicarTabela

from .servicos import (
    ConfiguracaoInvalida,
    estado_configuracao,
    estado_publico,
    salvar_configuracao,
)
from .throttling import ValoresPublicoThrottle


def _sem_cache(resposta: Response) -> Response:
    # Atrás da Cloudflare, um valor cacheado na borda é justamente a trava velha
    # que estas rotas existem para evitar.
    resposta["Cache-Control"] = "no-store"
    return resposta


class ValoresView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ValoresPublicoThrottle]

    def get(self, request):
        return _sem_cache(Response(estado_publico()))


class ConfiguracaoView(AceitaAssercao, APIView):
    """Valor e trava de cada modalidade. Só diretoria, sem teto por IP."""

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def get(self, request):
        return _sem_cache(Response(estado_configuracao()))

    def put(self, request):
        try:
            salvar_configuracao(request.data, autor=request.user)
        except ConfiguracaoInvalida as erro:
            return _sem_cache(recusa(request, str(erro)))
        return _sem_cache(Response({"ok": True, **estado_configuracao()}))
