"""
Os dois endpoints do comprovante de venda.

    POST /api/venda/comprovante   o navegador pede, antes de enviar ao n8n
    POST /api/venda/validar       o n8n confere, antes de processar

O primeiro é ANÔNIMO, e tem que ser: quem monta a venda é o consultor, que não
faz login. A credencial entra só quando há negociação — e aí vem no `Authorization`,
do modal de autorização.

O segundo é fechado por um segredo compartilhado com o n8n. Não é firula: sem
ele, qualquer um poderia queimar os nonces das vendas alheias chamando o
endpoint em laço, e as vendas legítimas passariam a ser recusadas.
"""
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .servicos import VendaRecusada, emitir, validar
from .throttling import ComprovanteThrottle

logger = logging.getLogger(__name__)


def _ip(request):
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class JWTOpcional(JWTAuthentication):
    """JWT quando vier, anônimo quando não vier — sem 401 no meio do caminho.

    A `JWTAuthentication` padrão levanta em token malformado, e isso derrubaria
    a venda a preço de tabela de um consultor cuja autorização tinha acabado de
    expirar. Aqui, token ruim significa "sem autorização": se a venda for
    negociada, o serviço recusa com uma mensagem que explica o que fazer.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except Exception:
            return None


@api_view(["POST"])
@authentication_classes([JWTOpcional])
@permission_classes([AllowAny])
@throttle_classes([ComprovanteThrottle])
def emitir_comprovante(request):
    """Assina os valores da venda, se eles se sustentarem."""
    try:
        comprovante = emitir(
            request.data or {},
            usuario=request.user if request.user.is_authenticated else None,
            ip=_ip(request),
        )
    except VendaRecusada as erro:
        # 422 e não 400: o corpo está bem formado, o que não passa é a regra de
        # negócio. Ajuda a separar bug de front de tentativa de burla no log.
        logger.warning("comprovante recusado: %s", erro)
        return Response(
            {"erro": str(erro)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )

    return Response(
        {
            "comprovante": comprovante.token,
            "expira_em": comprovante.expira_em.isoformat(),
        }
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def validar_comprovante(request):
    """Confere e consome o comprovante. Só o n8n chama isto.

    A resposta é sempre 200 com `{valido: bool, motivo}`: o n8n precisa de algo
    que ele consiga ramificar num nó IF. Devolver 4xx faria o nó de HTTP falhar
    e a venda sumir sem deixar rastro em lugar nenhum.
    """
    esperado = getattr(settings, "LOBBY_N8N_TOKEN", "")
    if not esperado:
        logger.error("LOBBY_N8N_TOKEN não configurado — validação indisponível")
        return Response(
            {"erro": "Validação de venda não configurada."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    recebido = request.META.get("HTTP_AUTHORIZATION", "")
    if recebido != f"Bearer {esperado}":
        return Response(
            {"erro": "Não autorizado."}, status=status.HTTP_401_UNAUTHORIZED
        )

    corpo = request.data or {}
    resultado = validar(corpo.get("comprovante"), corpo.get("valores") or {})

    if not resultado["valido"]:
        logger.warning(
            "venda recusada na validação: motivo=%s protocolo=%s",
            resultado.get("motivo"),
            (corpo.get("valores") or {}).get("protocolo"),
        )
    return Response(resultado)
