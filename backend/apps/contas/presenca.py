"""
`POST /api/presenca` — o sinal de presença da tela do Lobby (TSK-1520).

O Conecta tem o painel "Uso em tempo real" (só SuperAdmin): quem está online
agora em cada sistema, e em qual tela. A tela do Lobby (`index.html`, o
`sinalDePresenca`) manda um sinal por minuto enquanto a aba está aberta, na
troca de tela e na volta à aba, com o módulo, a tela e o momento da última
interação (clique, tecla, gesto de rolagem, Voltar/Avançar). Esta rota o repassa
ao Conecta pelo `identidade_client.enviar_presenca` (cliente 1.6.0), com a
AppKey de sempre (`IDENTIDADE_APP_KEY`). Regras do Mathias, 16/11/2026: online
= sinal nos últimos 2 min; parado = sem interação há mais de 3 min. Quem decide
as duas coisas é o Conecta: daqui sai só o sinal.

- **Só a sessão da TELA.** A autenticação é a do login do Lobby (TSK-1513), o
  `JWTRevalidado`, declarada aqui e não herdada do padrão: se um dia o padrão
  ganhar outra classe, esta rota não muda junto. As outras credenciais do Lobby
  nunca chegam ao `enviar_presenca`: a asserção do Conecta (`X-Conecta-Assercao`)
  leva o 403 `ROTA_NAO_LIBERADA` do `FechaRotasForaDaLista` antes da view; o
  segredo do n8n e o token do Prometheus não são JWT do Lobby (401); a sessão do
  /django-admin/ e o cabeçalho `X-Lobby-Autorizacao` não são lidos (401). Um
  agente, um cron ou uma integração que chamasse esta rota faria a pessoa
  aparecer online sem estar. (O token que o modal do gerente recebe é um JWT de
  sessão como o do login: o lobby o manda só no `X-Lobby-Autorizacao` do
  comprovante, nunca no `Authorization`.)
- **204 sempre que o corpo vale**, com o Conecta fora, lento, em manutenção ou
  recusando a chave: o `enviar_presenca` nunca levanta, tem teto de 2 s e pausa
  o processo por 60 s depois de uma falha. O sinal é acessório, e a tela não faz
  nada com a resposta.
- **O freio do Conecta lento** (revisão do Codex): o repasse é dentro da
  requisição, e o backend tem 3 workers síncronos. Com o Conecta fora ou
  pendurado, a pausa do cliente já limita a uma chamada presa por worker por
  minuto; com ele LENTO mas respondendo (1 a 2 s), não há falha, e cada sinal
  prenderia um worker — com dezenas de abas abertas, catálogo e comprovante
  entrariam na fila. Por isso, o repasse que leva mais de `LENTO_DEMAIS` faz
  este processo ficar `PAUSA_DEPOIS_DE_LENTO` sem repassar (responde 204 do
  mesmo jeito), com uma linha no log. A resolução do nome do Conecta não tem
  teto no socket (ver o `enviar_presenca`): é o mesmo risco do login e da
  revalidação da sessão, e a pausa do cliente o limita a uma vez por minuto.
- **400** para o corpo sem a forma abaixo; **401** sem a sessão.
- **Teto por pessoa:** `LOBBY_RATE_PRESENCA` (20/min). A tela manda 1 por minuto
  por aba, mais 1 por troca de tela e 1 por volta à aba (no máximo a cada 30 s).
- **Nada fica gravado aqui:** sem model, sem migration, sem cache próprio, sem
  linha de log da aplicação por sinal. O Conecta guarda só o estado de agora. (O
  teto e a revalidação da sessão usam o cache de sempre, que não guarda a tela
  de ninguém; o access log do gunicorn e do nginx registra o POST, como o de
  qualquer rota, sem o corpo.)

Corpo:

- `modulo`: texto, obrigatório, até 60 (ex.: "Elite", "APN", "Lobby");
- `tela`: texto, opcional, até 120 (ex.: "Pagamento · ELITE PRO");
- `ultima_interacao`: opcional; ISO 8601 COM o fuso (o `toISOString()` do
  navegador). Sem fuso não se sabe que hora é. (A do futuro, do relógio
  adiantado, o Conecta lê como agora.)

Os tamanhos são os que o Conecta grava; a tela já manda cortado.
"""
import logging
import time

from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

import identidade_client

from .autenticacao import JWTRevalidado
from .throttling import PresencaThrottle

logger = logging.getLogger(__name__)

TAMANHO_DO_MODULO = 60
TAMANHO_DA_TELA = 120
# O freio do Conecta lento (ver o docstring). Em segundos. O Conecta responde
# o sinal em décimos de segundo; um segundo inteiro já é ele com problema.
LENTO_DEMAIS = 1.0
PAUSA_DEPOIS_DE_LENTO = 60

_PAUSADO_ATE = 0.0
_relogio = time.monotonic


class SinalDePresencaSerializer(serializers.Serializer):
    modulo = serializers.CharField(max_length=TAMANHO_DO_MODULO)
    tela = serializers.CharField(
        max_length=TAMANHO_DA_TELA, required=False, allow_blank=True, default=""
    )
    ultima_interacao = serializers.CharField(
        required=False, allow_null=True, default=None
    )

    def validate_ultima_interacao(self, valor):
        if valor is None:
            return None
        try:
            quando = parse_datetime(valor)
        except (ValueError, OverflowError):
            # Bem formada, mas impossível (mês 13, fuso de 25 horas).
            quando = None
        if quando is None or quando.tzinfo is None:
            raise serializers.ValidationError(
                "A última interação vai em ISO 8601, com o fuso."
            )
        return quando


@api_view(["POST"])
@authentication_classes([JWTRevalidado])
@permission_classes([IsAuthenticated])
@throttle_classes([PresencaThrottle])
def presenca(request):
    if not isinstance(request.data, dict):
        # Lista, texto solto ou corpo vazio: não é um sinal.
        return Response(
            {"erro": "O sinal vai num objeto JSON."}, status=status.HTTP_400_BAD_REQUEST
        )
    sinal = SinalDePresencaSerializer(data=request.data)
    sinal.is_valid(raise_exception=True)
    _repassa(request.user, sinal.validated_data)
    return Response(status=status.HTTP_204_NO_CONTENT)


def _repassa(usuario, dados):
    """O `enviar_presenca`, com o freio do Conecta lento. Nunca levanta."""
    global _PAUSADO_ATE
    inicio = _relogio()
    if inicio < _PAUSADO_ATE:
        return
    # Devolve True/False e nunca levanta: o resultado não muda a resposta.
    identidade_client.enviar_presenca(
        usuario,
        modulo=dados["modulo"],
        tela=dados["tela"],
        ultima_interacao=dados["ultima_interacao"],
    )
    fim = _relogio()
    levou = fim - inicio
    if levou > LENTO_DEMAIS:
        _PAUSADO_ATE = fim + PAUSA_DEPOIS_DE_LENTO
        logger.warning(
            "presença: o repasse ao Conecta levou %.1f s; sem sinal deste processo por %s s",
            levou, PAUSA_DEPOIS_DE_LENTO,
        )
