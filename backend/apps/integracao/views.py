"""
As rotas novas do Lobby para o Admin Lobby do Conecta (ADR-002 §13.4).

    GET   /api/integracao/quem-sou            quem é a pessoa da asserção, e se publica
    GET   /api/integracao/saude               quais `kid` este servidor aceita

Todas sob `/api/integracao/`, sem barra final como o resto do Lobby, e todas SÓ
com a asserção (`assercao_obrigatoria`): o JWT de sessão do /admin não entra
aqui. Sem chave pública configurada, 503 `INTEGRACAO_DESLIGADA`.

A permissão é do Lobby, sempre (§2.3): o Conecta diz quem é a pessoa; o
`VinculoIdentidade.papel` diz o que ela pode.
"""
from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contas.autenticacao_conecta import AUDIENCIA, AceitaAssercao, kids_aceitos


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
