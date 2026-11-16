"""
Os dois endpoints do comprovante de venda.

    POST /api/venda/comprovante   o navegador pede, antes de enviar ao n8n
    POST /api/venda/validar       o n8n confere, antes de processar

O primeiro pede a SESSÃO de quem vende, no `Authorization` (TSK-1513; até
então era anônimo). A credencial de quem AUTORIZA a negociação é outra coisa e
vem noutro cabeçalho, o `X-Lobby-Autorizacao: Bearer <token>`: o token do
gerente que digitou a senha no modal, ou o da própria sessão quando quem vende já
é gerente ou diretoria e clicou em "autorizar com a minha conta". O consultor
nunca é o autorizador por estar logado: sem o cabeçalho, venda negociada é
recusada como sempre foi.

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
from rest_framework import HTTP_HEADER_ENCODING
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.contas.autenticacao import JWTRevalidado
from config.real_ip import ip_do_cliente

from .servicos import VendaRecusada, emitir, validar
from .throttling import ComprovanteThrottle

logger = logging.getLogger(__name__)


class JWTOpcional(JWTRevalidado):
    """JWT quando vier, ninguém quando não vier — sem 401 no meio do caminho.

    A `JWTAuthentication` padrão levanta em token malformado, e isso derrubaria
    a venda a preço de tabela de um consultor cuja autorização tinha acabado de
    expirar. Aqui, token ruim significa "sem autorização": se a venda for
    negociada, o serviço recusa com uma mensagem que explica o que fazer.

    Revalidado no Conecta (TSK-1238), e é aqui que mais importa: é este o token
    que destrava a venda negociada. O de quem foi desativado lá vira "sem
    autorização", como o vencido.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except Exception:
            return None


class AutorizacaoDaNegociacao(JWTOpcional):
    """O token de quem autoriza, lido do `X-Lobby-Autorizacao` (TSK-1513).

    Desde que o comprovante pede a sessão de quem vende no `Authorization`, o
    token de quem autoriza não cabe mais lá. Mesmo formato (`Bearer <token>`) e
    mesma regra do `JWTOpcional`: ruim, vencido ou de quem o Conecta desativou
    vira "sem autorização", e a venda negociada recebe o 422 de sempre.

    Não é classe de autenticação da view: é usada à mão, depois que a sessão de
    quem vende já passou, para descobrir o autorizador.
    """

    def get_header(self, request):
        cabecalho = request.META.get("HTTP_X_LOBBY_AUTORIZACAO")
        if isinstance(cabecalho, str):
            cabecalho = cabecalho.encode(HTTP_HEADER_ENCODING)
        return cabecalho


def autorizador_da_negociacao(request):
    """Quem autorizou a negociação desta venda, ou None."""
    resultado = AutorizacaoDaNegociacao().authenticate(request)
    return resultado[0] if resultado else None


@api_view(["POST"])
@authentication_classes([JWTRevalidado])
@permission_classes([IsAuthenticated])
@throttle_classes([ComprovanteThrottle])
def emitir_comprovante(request):
    """Assina os valores da venda, se eles se sustentarem.

    `usuario` do `emitir` é quem AUTORIZOU (o `X-Lobby-Autorizacao`), nunca quem
    está logado vendendo: é ele que fica em `autorizado_por` no comprovante.
    """
    try:
        comprovante = emitir(
            request.data or {},
            usuario=autorizador_da_negociacao(request),
            ip=ip_do_cliente(request),
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
