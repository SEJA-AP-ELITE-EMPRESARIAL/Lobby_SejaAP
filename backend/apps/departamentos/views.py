"""
GET /api/departamentos — a lista do dropdown do Elite e da APN.

Pública pelo mesmo motivo do catálogo: o consultor não faz login. Lê o banco,
nunca o Omie (ver `models.py`), então responde na mesma velocidade com o Omie de
pé ou fora do ar.

    { "versao": "<hash>", "sincronizado_em": "<ISO>|null",
      "departamentos": [{"codigo", "descricao", "estrutura"}, ...] }

Só os ATIVOS. O front compara `versao` com a que já tem para saber se a lista
mudou desde que a página abriu.
"""
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .servicos import estado_publico
from .throttling import DepartamentosPublicoThrottle


class DepartamentosView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [DepartamentosPublicoThrottle]

    def get(self, request):
        resposta = Response(estado_publico())
        # Atrás da Cloudflare, uma lista cacheada na borda é justamente a lista
        # velha que esta rota existe para evitar.
        resposta["Cache-Control"] = "no-store"
        return resposta
