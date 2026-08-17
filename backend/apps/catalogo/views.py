"""
O endpoint do catálogo — uma URL, dois métodos, duas permissões.

    GET /api/catalogo   público   — todo consultor, sem credencial
    PUT /api/catalogo   restrito  — publica a tabela de preços

A URL é a MESMA para os dois, e isso não é negociável: o `index.html:2218` faz
GET e o `admin.html:222` faz PUT no mesmo caminho literal, e ambos são arquivos
estáticos que não mudam junto com o backend. É por isso que aqui há uma classe
com `get_permissions()` por método, em vez de duas views com `@api_view`.

O contrato do envelope é o mesmo do KV (`functions/api/catalogo.js`, removido da
árvore — `git show 338e932`; ver a nota em `models.py`):

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

from . import produtos as servico_produtos
from .cobranca import CobrancaInvalida, estado_atual, publica_politica
from .models import Categoria, PoliticaCobranca, PublicacaoCatalogo, Vigencia
from .serializers import serializa_catalogo, serializa_politica, serializa_produto
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
        # `politica_cobranca` no prefetch: sem ele, cada produto vira uma consulta
        # a mais atrás de uma exceção que quase nenhum tem.
        return serializa_catalogo(
            Categoria.objects.prefetch_related("produtos__politica_cobranca").all()
        )

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
                    # A regra de data do cronograma. Vem no MESMO envelope do
                    # preço porque o front precisa das duas coisas na mesma
                    # abertura — uma chamada a mais no caminho crítico da venda
                    # seria uma chance a mais de o consultor cotar com metade da
                    # configuração.
                    "cobranca": serializa_politica(PoliticaCobranca.geral_atual()),
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
                    "cobranca": serializa_politica(PoliticaCobranca.geral_atual()),
                    "atualizadoEm": self._atualizado_em(),
                    "alteracoes": [str(a) for a in alteracoes],
                }
            )
        )


class CobrancaView(APIView):
    """A regra de data do cronograma — leitura pública, escrita da diretoria.

    Leitura pública pelo mesmo motivo do catálogo: o consultor não faz login, e
    sem a política ele não consegue montar cronograma nenhum. Na prática o
    `index.html` lê a política junto do catálogo, num envelope só; esta rota
    existe para a TELA da diretoria, que também precisa das exceções e das opções
    válidas de cada campo.

    Escrita é diretoria, e não gerente: a data de cobrança vale para todas as
    vendas seguintes — mesmo alcance da tabela de preços. Mudar a data de UMA
    venda continua sendo autorização de gerente, na tela do consultor.
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated(), PodePublicarTabela()]

    def get_throttles(self):
        return [CatalogoPublicoThrottle()] if self.request.method == "GET" else []

    @staticmethod
    def _sem_cache(resposta):
        resposta["Cache-Control"] = "no-store"
        return resposta

    def get(self, request):
        return self._sem_cache(Response(estado_atual()))

    def put(self, request):
        try:
            estado = publica_politica(request.data or {}, autor=request.user)
        except CobrancaInvalida as erro:
            return self._sem_cache(
                Response({"erro": str(erro)}, status=status.HTTP_400_BAD_REQUEST)
            )
        return self._sem_cache(Response({"ok": True, **estado}))


class ProdutosView(APIView):
    """Criar produto. Só diretoria.

    Fica separada do `PUT /api/catalogo` porque as duas operações têm garantias
    diferentes: publicar preço não cria nem apaga linha, por desenho (ver
    `servicos.py`). Misturar as duas no mesmo endpoint faria a promessa mais forte
    depender de qual campo veio no corpo.
    """

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def post(self, request):
        try:
            produto = servico_produtos.cria(request.data or {}, autor=request.user)
        except servico_produtos.ProdutoInvalido as erro:
            return Response({"erro": str(erro)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"ok": True, "produto": serializa_produto(produto)},
            status=status.HTTP_201_CREATED,
        )


class ProdutoView(APIView):
    """Editar um produto existente. Só diretoria.

    Não há DELETE, e é decisão: produto vendido aparece no protocolo de vendas
    fechadas. Ver o cabeçalho de `produtos.py`.
    """

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def patch(self, request, categoria_slug, slug):
        try:
            produto = servico_produtos.edita(
                categoria_slug, slug, request.data or {}, autor=request.user
            )
        except servico_produtos.ProdutoInvalido as erro:
            return Response({"erro": str(erro)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "produto": serializa_produto(produto)})


class HistoricoView(APIView):
    """A linha do tempo: o que valeu, quando, e por decisão de quem.

    Fechada para a diretoria. Não é dado sensível como senha, mas é a série
    histórica de preço da empresa inteira — e o resto do app não precisa dela
    para funcionar, então não há motivo para deixá-la anônima como o catálogo.
    """

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    LIMITE_PADRAO = 200
    LIMITE_MAX = 1000

    def get(self, request):
        consulta = Vigencia.objects.all()

        chave = (request.query_params.get("chave") or "").strip()
        if chave:
            consulta = consulta.filter(chave=chave)
        campo = (request.query_params.get("campo") or "").strip()
        if campo:
            consulta = consulta.filter(campo=campo)

        try:
            limite = min(int(request.query_params.get("limite") or self.LIMITE_PADRAO), self.LIMITE_MAX)
        except (TypeError, ValueError):
            limite = self.LIMITE_PADRAO

        rotulos = dict(Vigencia.Campo.choices)
        registros = [
            {
                "chave": v.chave,
                "rotulo": v.rotulo,
                "campo": v.campo,
                "campo_rotulo": rotulos.get(v.campo, v.campo),
                "valor": v.valor,
                "vigente_de": v.vigente_de.isoformat(),
                "vigente_ate": v.vigente_ate.isoformat() if v.vigente_ate else None,
                "vigente": v.vigente_ate is None,
                # Sem autor = semente ou migração. A tela mostra "sistema", e é
                # honesto: não houve pessoa.
                "autor": v.autor_email or None,
            }
            for v in consulta.select_related("autor")[: max(1, limite)]
        ]
        resposta = Response({"registros": registros, "total": len(registros)})
        resposta["Cache-Control"] = "no-store"
        return resposta
