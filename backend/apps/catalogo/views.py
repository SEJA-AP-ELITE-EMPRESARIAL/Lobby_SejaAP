"""
O endpoint do catálogo — uma URL, dois métodos, duas permissões.

    GET /api/catalogo   público   — todo consultor, sem credencial
    PUT /api/catalogo   restrito  — publica a tabela de preços

A URL é a MESMA para os dois, e isso não é negociável: o `index.html:2218` faz
GET e o `admin.html:222` faz PUT no mesmo caminho literal, e ambos são arquivos
estáticos que não mudam junto com o backend. É por isso que aqui há uma classe
com `get_permissions()` por método, em vez de duas views com `@api_view`.

O contrato do envelope é o mesmo do KV (`functions/api/catalogo.js`):

    { "cats": [...], "atualizadoEm": "<ISO>|null", "origem": "banco" }

Regras que não podem se perder na migração:

- **Sempre JSON, inclusive em erro.** O `admin.html:174` faz `await r.json()`
  ANTES de checar `r.ok`; uma página de erro HTML do Django estoura a tela antes
  de chegar ao tratamento.
- **`Cache-Control: no-store` em tudo.** O front pede `cache: 'no-store'`
  (`index.html:2218`), mas quem garante é a resposta — atrás da Cloudflare, uma
  tabela de preços cacheada na borda é venda fechada com valor errado.
- **401 derruba a sessão do admin** (`admin.html:228`), então 401 é só para
  "não autenticado". Autenticado sem permissão de publicar recebe 403.
"""
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contas.permissions import PodePublicarTabela

from .models import Categoria, PublicacaoCatalogo
from .serializers import serializa_catalogo
from .servicos import CatalogoInvalido, publica_catalogo
from .throttling import CatalogoPublicoThrottle


class CatalogoView(APIView):
    """Leitura pública, escrita restrita, no mesmo caminho."""

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        # Publicar a tabela é só da diretoria. Gerente autoriza negociação numa
        # venda — alcance de uma venda; a tabela vale para todas as próximas.
        return [IsAuthenticated(), PodePublicarTabela()]

    def get_throttles(self):
        # Só a leitura anônima precisa de teto por IP. A escrita já é limitada
        # por exigir credencial — e um teto ali atrapalharia a diretoria
        # corrigindo um preço errado às pressas.
        if self.request.method == "GET":
            return [CatalogoPublicoThrottle()]
        return []

    # ----- helpers -------------------------------------------------------

    @staticmethod
    def _sem_cache(resposta: Response) -> Response:
        resposta["Cache-Control"] = "no-store"
        return resposta

    @staticmethod
    def _ultima_publicacao():
        return PublicacaoCatalogo.objects.order_by("-publicado_em").first()

    @classmethod
    def _atualizado_em(cls):
        ultima = cls._ultima_publicacao()
        # O `admin.html:289` joga isto num `new Date(...)`: ISO-8601 ou null,
        # nunca string livre — senão vira "Invalid Date" na tela da diretoria.
        return ultima.publicado_em.isoformat() if ultima else None

    @staticmethod
    def _cats():
        return serializa_catalogo(Categoria.objects.prefetch_related("produtos").all())

    # ----- métodos -------------------------------------------------------

    def get(self, request):
        """A tabela de preços que o lobby carrega ao abrir.

        Anônimo de propósito: qualquer consultor cota e cadastra sem login. O que
        exige credencial é NEGOCIAR (alterar valor), e isso não passa por aqui.
        """
        return self._sem_cache(
            Response(
                {
                    "cats": self._cats(),
                    "atualizadoEm": self._atualizado_em(),
                    # Ninguém lê, mas o envelope do KV tinha o campo. Mantido para
                    # que a migração não seja o momento de descobrir um consumidor
                    # esquecido.
                    "origem": "banco",
                }
            )
        )

    def put(self, request):
        """Publica a tabela de preços editada no /admin.

        O corpo é `{cats: [...]}` com o catálogo inteiro, incluindo a APN — que o
        serviço descarta, porque categoria de fluxo próprio não tem preço de
        tabela. A resposta devolve o catálogo COM a APN reinserida; sem isso ela
        desapareceria da tela do admin logo depois de publicar.
        """
        cats = (request.data or {}).get("cats")
        try:
            catalogo, alteracoes = publica_catalogo(cats, autor=request.user)
        except CatalogoInvalido as erro:
            return self._sem_cache(
                Response({"erro": str(erro)}, status=status.HTTP_400_BAD_REQUEST)
            )

        return self._sem_cache(
            Response(
                {
                    "ok": True,
                    "cats": catalogo,
                    "atualizadoEm": self._atualizado_em(),
                    "alteracoes": [str(a) for a in alteracoes],
                }
            )
        )
