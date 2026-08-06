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
from rest_framework.views import exception_handler as handler_padrao


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

    # O catálogo nunca pode ser cacheado, e resposta de erro menos ainda.
    resposta["Cache-Control"] = "no-store"
    return resposta
