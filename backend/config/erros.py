"""
Um formato de erro só para toda a API: `{"erro": "<frase>"}`.

O DRF devolve `{"detail": "..."}` por padrão, e o front do Lobby lê `d.erro`
(`admin.html`, no tratamento do PUT, e o modal de autorização do `index.html`).
Sem esta tradução, os erros que o DRF gera sozinho — 401 sem credencial, 403 de
permissão, 429 de throttle — chegariam ao usuário como "Falha ao salvar", a
mensagem genérica de fallback, escondendo justamente o que ele precisa saber:

    "Somente a diretoria pode alterar a tabela de preços."

As views escrevem `{"erro": ...}` diretamente; este handler cuida do que o
framework levanta por conta própria. Mesma ideia do `conecta-id/apps/identidade/
erros.py`, que padroniza o contrato de erro do serviço central.
"""
import re

from django.views.debug import SafeExceptionReporterFilter
from rest_framework import exceptions
from rest_framework.views import exception_handler as handler_padrao

# O cabeçalho da asserção do Conecta (TSK-1059). Repetido aqui, e não importado
# de `apps.contas`, para este módulo continuar carregando antes dos apps.
CABECALHO_DA_ASSERCAO = "HTTP_X_CONECTA_ASSERCAO"

# Código de cada recusa genérica do DRF quando quem chamou foi o Conecta. O
# Conecta decide pelo `codigo`, nunca pela frase (ADR-002 §4.7).
CODIGOS_DA_INTEGRACAO = (
    (exceptions.Throttled, "MUITAS_CHAMADAS"),
    (exceptions.PermissionDenied, "SEM_PERMISSAO"),
    (exceptions.NotFound, "NAO_ENCONTRADA"),
    (exceptions.MethodNotAllowed, "ROTA_NAO_LIBERADA"),
    (exceptions.ParseError, "DADOS_INVALIDOS"),
    (exceptions.ValidationError, "DADOS_INVALIDOS"),
)


def _frase(detalhe) -> str:
    """Achata o corpo do DRF numa frase só.

    O `detail` pode ser string, lista ou dicionário de campo → lista de erros.
    O front exibe o texto cru, então o que sai daqui precisa ser legível por
    uma pessoa, não um JSON serializado.
    """
    if isinstance(detalhe, str):
        return detalhe
    if isinstance(detalhe, list):
        return " ".join(_frase(item) for item in detalhe)
    if isinstance(detalhe, dict):
        return " ".join(_frase(valor) for valor in detalhe.values())
    return str(detalhe)


def _codigo(exc, contexto) -> str | None:
    """O `codigo` da resposta, quando ela é para o Conecta.

    As recusas da asserção já trazem o seu (`exc.codigo`). As genéricas do DRF —
    403 da permissão de diretoria, 429 do teto por pessoa — ganham um aqui, mas
    só quando a chamada veio com a asserção: o /admin de hoje continua
    recebendo `{"erro": ...}` e nada mais.
    """
    codigo = getattr(exc, "codigo", None)
    if codigo:
        return codigo
    request = contexto.get("request")
    if request is None or CABECALHO_DA_ASSERCAO not in request.META:
        return None
    for classe, codigo in CODIGOS_DA_INTEGRACAO:
        if isinstance(exc, classe):
            return codigo
    return None


def tratar_excecao(exc, contexto):
    resposta = handler_padrao(exc, contexto)
    if resposta is None:
        # Exceção não tratada: deixa subir para o Django, que registra no log e
        # responde 500. Engolir aqui esconderia bug de verdade.
        return None

    corpo = resposta.data
    if isinstance(corpo, dict) and "erro" not in corpo:
        detalhe = corpo.get("detail", corpo)
        resposta.data = {"erro": _frase(detalhe)}
    elif not isinstance(corpo, dict):
        resposta.data = {"erro": _frase(corpo)}

    codigo = _codigo(exc, contexto)
    if codigo:
        # O corpo do ADR (`detail` + `codigo`) ao lado do `erro` da casa: o
        # Conecta lê o primeiro, as telas do Lobby leem o segundo.
        frase = resposta.data.get("erro", "")
        resposta.data = {"erro": frase, "detail": frase, "codigo": codigo}

    # O catálogo nunca pode ser cacheado, e resposta de erro menos ainda.
    resposta["Cache-Control"] = "no-store"
    return resposta


class FiltroDeRelatorio(SafeExceptionReporterFilter):
    """Censura a asserção do Conecta no relatório de erro do Django.

    O filtro padrão esconde o que casa com API|AUTH|TOKEN|KEY|SECRET|PASS|
    SIGNATURE — e `HTTP_X_CONECTA_ASSERCAO` não casa com nenhum. A asserção não
    pode ir para log nenhum, nem inteira nem em pedaço (ADR-002 §10.2).
    """

    hidden_settings = re.compile(
        SafeExceptionReporterFilter.hidden_settings.pattern + "|ASSERCAO",
        flags=re.IGNORECASE,
    )
