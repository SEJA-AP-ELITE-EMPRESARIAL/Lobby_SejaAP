"""
As rotas novas do Lobby para o Admin Lobby do Conecta (ADR-002 §13.4).

    GET   /api/integracao/quem-sou            quem é a pessoa da asserção, e se publica
    GET   /api/integracao/saude               quais `kid` este servidor aceita
    POST  /api/integracao/catalogo/publicar   valores e produtos, num clique, tudo ou nada
    PATCH /api/integracao/categorias/<slug>   liberar ou tirar a categoria do Lobby
    GET   /api/integracao/historico           o histórico unificado, filtrado e paginado

Todas sob `/api/integracao/`, sem barra final como o resto do Lobby, e todas SÓ
com a asserção (`assercao_obrigatoria`): o JWT de sessão do /admin não entra
aqui. Sem chave pública configurada, 503 `INTEGRACAO_DESLIGADA`.

A permissão é do Lobby, sempre (§2.3): o Conecta diz quem é a pessoa; o
`VinculoIdentidade.papel` diz o que ela pode.
"""
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalogo.models import Categoria
from apps.catalogo.publicacao import PublicacaoInvalida, publica_edicoes
from apps.catalogo.travas import altera_travada
from apps.contas.autenticacao_conecta import AUDIENCIA, AceitaAssercao, kids_aceitos, recusa
from apps.contas.permissions import PodePublicarTabela

from . import historico


def _sem_cache(resposta: Response) -> Response:
    resposta["Cache-Control"] = "no-store"
    return resposta


class RotaDeIntegracao(AceitaAssercao, APIView):
    """Base das rotas novas: só a asserção, e pessoa com conta no Lobby."""

    assercao_obrigatoria = True
    permission_classes = [IsAuthenticated]


class QuemSouView(RotaDeIntegracao):
    """Para a tela do Conecta abrir em "só leitura" quando a pessoa não publica.

    Responde 200 para qualquer pessoa com conta, com ou sem papel: é exatamente
    o caso de quem só lê que esta rota existe para contar. Em "Entrar como
    usuário" (`imp`), `podePublicarTabela` sai falso — as escritas seriam
    recusadas — e `impersonacao` diz por quê.
    """

    def get(self, request):
        usuario = request.user
        vinculo = getattr(usuario, "vinculo_identidade", None)
        impersonacao = bool(request.auth.impersonacao)
        return _sem_cache(
            Response(
                {
                    "nome": usuario.get_full_name() or usuario.email,
                    "email": usuario.email,
                    "papel": vinculo.papel if vinculo else "",
                    "podePublicarTabela": bool(vinculo and vinculo.pode_publicar_tabela)
                    and not impersonacao,
                    "impersonacao": impersonacao,
                }
            )
        )


class SaudeView(RotaDeIntegracao):
    """Quais `kid` o Lobby aceita, para a troca de chave sem queda (§8.2, passo 3).

    Se um destino lista só um `kid` no meio de uma troca, alguém subiu só metade.
    """

    def get(self, request):
        return _sem_cache(
            Response(
                {
                    "kids": kids_aceitos(),
                    "emissor": settings.CONECTA_ASSERCAO_EMISSOR,
                    "audiencia": AUDIENCIA,
                }
            )
        )


class PublicarCatalogoView(RotaDeIntegracao):
    """Publica, numa transação só, os valores e os produtos editados.

    Uma `PublicacaoCatalogo` por clique, com autor (a pessoa do `sub`) e a lista
    de → para. Ver `apps/catalogo/publicacao.py`.
    """

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def post(self, request):
        try:
            publicacao, linhas = publica_edicoes(request.data, autor=request.user)
        except PublicacaoInvalida as erro:
            return _sem_cache(recusa(request, str(erro), codigo=erro.codigo))
        return _sem_cache(
            Response(
                {
                    "ok": True,
                    "publicacao": {
                        "publicado_em": publicacao.publicado_em.isoformat(),
                        "autor": publicacao.autor_email or None,
                        "alteracoes": linhas,
                    },
                    "cats": publicacao.catalogo,
                }
            )
        )


class CategoriaView(RotaDeIntegracao):
    """`{"locked": true|false}`: tira a categoria do Lobby ou a libera.

    Travada, ela aparece como "Em implementação" e não abre, para todo consultor
    no instante seguinte. Até a TSK-1059 isto só mudava pelo /django-admin/, sem
    autor; agora fica na linha do tempo (ver `apps/catalogo/travas.py`).
    """

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def patch(self, request, slug):
        dados = request.data
        if not isinstance(dados, dict) or set(dados) != {"locked"} or not isinstance(dados["locked"], bool):
            return _sem_cache(recusa(request, 'Mande só {"locked": true} ou {"locked": false}.'))
        categoria = Categoria.objects.filter(slug=slug).first()
        if categoria is None:
            return _sem_cache(
                recusa(request, f'Categoria "{slug}" não existe.', status.HTTP_404_NOT_FOUND, "NAO_ENCONTRADA")
            )
        alteracao = altera_travada(categoria, dados["locked"], autor=request.user)
        categoria.refresh_from_db()
        return _sem_cache(
            Response(
                {
                    "ok": True,
                    "categoria": {"id": categoria.slug, "nome": categoria.nome, "locked": categoria.travada},
                    "alteracao": alteracao,
                }
            )
        )


class HistoricoView(RotaDeIntegracao):
    """O histórico unificado. Só diretoria, como o `/api/historico` antigo: é a
    série histórica de preço e de configuração da empresa inteira."""

    permission_classes = [IsAuthenticated, PodePublicarTabela]

    def get(self, request):
        try:
            return _sem_cache(Response(historico.consulta(request.query_params)))
        except historico.FiltroInvalido as erro:
            return _sem_cache(recusa(request, str(erro)))
