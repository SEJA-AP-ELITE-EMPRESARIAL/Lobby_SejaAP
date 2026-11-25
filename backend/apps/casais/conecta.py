"""
A pergunta ao Conecta: "este CNPJ é cliente ativo?" (TSK-1622).

A rota é da TSK-1621 (ConectaAP): `POST {IDENTIDADE_URL}/api/v1/clientes/ativo`,
com `{"cnpj": "..."}` no corpo e a chave da aplicação `lobby` no
`Authorization` — a mesma do login e do sinal de presença (`IDENTIDADE_URL` e
`IDENTIDADE_APP_KEY`). Responde `{"cliente_ativo": true|false}`: ativo é o
cadastro com status Ativo (1) ou Em Aviso Prévio (7), fora dos internos e de
teste.

POR QUE NÃO NO `identidade_client.py`

Aquele arquivo é copiado byte a byte entre os apps (o `ClienteCopiadoTest`
confere o sha256), e não tem esta rota. Esta chamada é só do Lobby e mora aqui,
com o mesmo cuidado que o cliente tem:

- `User-Agent` próprio: a Cloudflare na frente do Conecta barra o
  `Python-urllib/3.x` (cliente 1.5.2);
- nenhum redirecionamento é seguido. Um 3xx vira "sem resposta", e a chave do
  app não sai daqui para outro destino (cliente 1.5.3). Aqui nem para o mesmo:
  a rota é uma só, e um 3xx nela já é configuração errada (a barra no fim dá 404
  no Conecta, e não 301);
- o CNPJ vai no corpo, nunca na URL: a URL vai para o access log dos proxies;
- teto curto (`TIMEOUT`): a pergunta é feita dentro da requisição do consultor.

O QUE SAI DAQUI

`cliente_ativo(cnpj)` devolve `True` ou `False` — só com o 200 do contrato — e
levanta `SemResposta` em todo o resto, com um `motivo` curto. Nem o 429 nem o
503 querem dizer `False`: o que fazer sem a resposta é decisão de quem chama
(`apps.casais.servicos`: a venda fica no preço cheio). Nada é guardado aqui; a
resposta guardada, a pausa depois de uma falha e o teto por pessoa são do
`servicos`.
"""
import http.client
import json
import logging
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

CAMINHO = "/api/v1/clientes/ativo"
USER_AGENT = "LobbySejaAP-Casais/1.0"
# Em segundos. O Conecta responde em décimos; mais que isto é ele com problema,
# e o consultor está esperando a tela. O `IDENTIDADE_TIMEOUT` menor vale.
TIMEOUT = 3
# A resposta do contrato é um objeto de uma chave. Mais que isto não é ela.
TAMANHO_MAXIMO = 64 * 1024


class SemResposta(Exception):
    """O Conecta não disse nem sim nem não.

    `motivo`: `nao_configurado`, `indisponivel`, `manutencao`, `limite` (o 429),
    `nao_autorizado` (401/403), `dados_invalidos` (400), `redirecionado` ou
    `resposta_inesperada`. `tentar_em`: os segundos do `Retry-After`, quando ele
    veio.
    """

    def __init__(self, motivo, tentar_em=None):
        super().__init__(motivo)
        self.motivo = motivo
        self.tentar_em = tentar_em


class _SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    """Não segue 3xx nenhum: `None` faz o `urllib` levantar o `HTTPError` dele."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_ABRIDOR = None


def _abrir(pedido, timeout):
    """O `urlopen`, sem seguir redirecionamento. Montado uma vez por processo."""
    global _ABRIDOR
    if _ABRIDOR is None:
        _ABRIDOR = urllib.request.build_opener(_SemRedirecionamento)
    return _ABRIDOR.open(pedido, timeout=timeout)


def _segundos(retry_after):
    """O `Retry-After` em segundos: número, ou data HTTP. `None` se não der para ler."""
    if not retry_after:
        return None
    texto = str(retry_after).strip()
    if texto.isdigit():
        return int(texto)
    try:
        quando = parsedate_to_datetime(texto)
    except (TypeError, ValueError, IndexError):
        return None
    if quando is None or quando.tzinfo is None:
        return None
    return max(0, int((quando - timezone.now()).total_seconds()))


def _codigo_do_erro(erro_http):
    try:
        corpo = json.loads(erro_http.read(TAMANHO_MAXIMO).decode())
    except Exception:
        return ""
    codigo = corpo.get("erro") if isinstance(corpo, dict) else None
    return codigo if isinstance(codigo, str) else ""


def _traduz(erro_http):
    """O `HTTPError` do Conecta, em `SemResposta`. Os de integração vão alto no log."""
    status = erro_http.code
    if 300 <= status < 400:
        logger.error(
            "clientes/ativo: o Conecta respondeu %s (redirecionamento) — confira o "
            "IDENTIDADE_URL; o redirecionamento não foi seguido", status,
        )
        return SemResposta("redirecionado")
    codigo = _codigo_do_erro(erro_http)
    if status == 429:
        tentar_em = _segundos(erro_http.headers.get("Retry-After"))
        logger.warning("clientes/ativo: limite do Conecta (429); tentar em %s s", tentar_em)
        return SemResposta("limite", tentar_em)
    if status == 503 and codigo == "manutencao":
        logger.warning("clientes/ativo: Conecta em manutenção")
        return SemResposta("manutencao", _segundos(erro_http.headers.get("Retry-After")))
    if status in (401, 403):
        logger.error(
            "clientes/ativo: o Conecta recusou a chave do Lobby (HTTP %s, erro=%s) — "
            "confira IDENTIDADE_APP_KEY e o IP do servidor na lista da aplicação lobby",
            status, codigo or "?",
        )
        return SemResposta("nao_autorizado")
    if status == 400:
        logger.error("clientes/ativo: o Conecta recusou o corpo (400, erro=%s)", codigo or "?")
        return SemResposta("dados_invalidos")
    logger.warning("clientes/ativo: Conecta sem resposta (HTTP %s, erro=%s)", status, codigo or "?")
    return SemResposta("indisponivel")


def cliente_ativo(cnpj):
    """`True`/`False` para o CNPJ, como o Conecta diz. Levanta `SemResposta`.

    `cnpj` em texto, já conferido por quem chama (`servicos.normaliza_cnpj`).
    """
    url = str(getattr(settings, "IDENTIDADE_URL", "") or "").rstrip("/")
    chave = getattr(settings, "IDENTIDADE_APP_KEY", "") or ""
    if not url or not chave:
        raise SemResposta("nao_configurado")
    timeout = min(TIMEOUT, getattr(settings, "IDENTIDADE_TIMEOUT", None) or TIMEOUT)

    try:
        pedido = urllib.request.Request(
            url + CAMINHO, data=json.dumps({"cnpj": cnpj}).encode(), method="POST"
        )
    except ValueError:
        logger.error("clientes/ativo: IDENTIDADE_URL inválida: %r", url)
        raise SemResposta("nao_configurado")
    pedido.add_header("Content-Type", "application/json")
    pedido.add_header("Accept", "application/json")
    pedido.add_header("User-Agent", USER_AGENT)
    # `add_unredirected_header`: mesmo que um dia alguém troque o abridor por um
    # que siga 3xx, a chave não vai junto.
    pedido.add_unredirected_header("Authorization", chave)

    try:
        with _abrir(pedido, timeout) as resposta:
            bruto = resposta.read(TAMANHO_MAXIMO)
    except urllib.error.HTTPError as erro:
        raise _traduz(erro) from erro
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError,
            ValueError) as erro:
        logger.warning("clientes/ativo: Conecta inalcançável (%s)", type(erro).__name__)
        raise SemResposta("indisponivel") from erro

    try:
        dados = json.loads(bruto)
    except ValueError:
        dados = None
    ativo = dados.get("cliente_ativo") if isinstance(dados, dict) else None
    if not isinstance(ativo, bool):
        logger.error("clientes/ativo: o Conecta respondeu 200 fora do contrato")
        raise SemResposta("resposta_inesperada")
    return ativo
