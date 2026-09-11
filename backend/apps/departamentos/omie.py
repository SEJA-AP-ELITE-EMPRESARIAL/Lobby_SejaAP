"""
Cliente mínimo da API do Omie — só a listagem de departamentos.

Mínimo de propósito. A credencial é do ERP inteiro, e este módulo é o único
lugar do Lobby que a usa: uma chamada de leitura, fixa no código. Nada aqui
aceita `call` vindo de fora.

DOIS DETALHES DA API QUE CUSTAM CARO SE ESQUECIDOS

- A chamada é `ListarDepartamentos`. Nome errado não dá 404: o Omie responde 500
  com `faultstring`, igual a qualquer outra falha. O mesmo vale para `app_key`
  com um caractere a mais (403, "chave de acesso inválida").
- Listagem sem registro nenhum TAMBÉM vem como falha ("Não existem registros
  para a página [1]!"). Tratar isso como "zero departamentos" desativaria a
  lista inteira; aqui é erro, e a lista do banco fica como estava.
"""
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

URL = "https://app.omie.com.br/api/v1/geral/departamentos/"
REGISTROS_POR_PAGINA = 50
# Rede de segurança contra um `total_de_paginas` absurdo. Em 11/09/2026 eram 18
# departamentos, uma página.
MAX_PAGINAS = 20


class OmieIndisponivel(Exception):
    """O Omie não entregou a lista. A mensagem vai para o log — nunca a chave."""


@dataclass(frozen=True)
class DepartamentoOmie:
    codigo: str
    descricao: str
    estrutura: str
    ativo: bool


def _faultstring(erro: urllib.error.HTTPError) -> str:
    try:
        return json.loads(erro.read().decode("utf-8")).get("faultstring") or "sem detalhe"
    except Exception:
        return "sem detalhe"


def _chama(pagina: int) -> dict:
    corpo = json.dumps(
        {
            "call": "ListarDepartamentos",
            "param": [{"pagina": pagina, "registros_por_pagina": REGISTROS_POR_PAGINA}],
            "app_key": settings.OMIE_APP_KEY,
            "app_secret": settings.OMIE_APP_SECRET,
        }
    ).encode("utf-8")
    pedido = urllib.request.Request(
        URL, data=corpo, headers={"Content-Type": "application/json"}, method="POST"
    )
    # `from None` em todos: a exceção original não carrega a chave, mas o
    # traceback encadeado é ruído no log de um laço que roda de hora em hora.
    try:
        with urllib.request.urlopen(pedido, timeout=settings.OMIE_TIMEOUT) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        # Antes do URLError, de quem é subclasse. Erro de negócio do Omie chega
        # aqui: status 500 (ou 403) com um JSON de `faultstring`.
        raise OmieIndisponivel(f"Omie respondeu {erro.code}: {_faultstring(erro)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise OmieIndisponivel(f"Sem conexão com o Omie: {erro}") from None
    except ValueError:
        raise OmieIndisponivel("O Omie respondeu algo que não é JSON.") from None


def listar_departamentos() -> list[DepartamentoOmie]:
    """Todos os departamentos do Omie, ativos e inativos, de todas as páginas."""
    if not (settings.OMIE_APP_KEY and settings.OMIE_APP_SECRET):
        raise OmieIndisponivel("OMIE_APP_KEY e OMIE_APP_SECRET não estão configurados.")

    departamentos: list[DepartamentoOmie] = []
    pagina = 1
    while True:
        dados = _chama(pagina)
        for item in dados.get("departamentos") or []:
            codigo = str(item.get("codigo") or "").strip()
            descricao = str(item.get("descricao") or "").strip()
            if not codigo or not descricao:
                logger.warning("Departamento do Omie sem código ou descrição, ignorado: %r", item)
                continue
            departamentos.append(
                DepartamentoOmie(
                    codigo=codigo,
                    descricao=descricao[:120],
                    estrutura=str(item.get("estrutura") or "").strip()[:60],
                    # O Omie diz "inativo": "S"/"N". Qualquer coisa que não seja
                    # um "S" explícito conta como ativo — é o default da API.
                    ativo=str(item.get("inativo") or "N").strip().upper() != "S",
                )
            )
        total = int(dados.get("total_de_paginas") or 1)
        if pagina >= min(total, MAX_PAGINAS):
            return departamentos
        pagina += 1
