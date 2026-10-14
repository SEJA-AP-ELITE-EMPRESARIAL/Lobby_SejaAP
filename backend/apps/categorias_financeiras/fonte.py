"""
De onde vêm as categorias financeiras: o `ListarCategorias` do Omie (TSK-879).

Mesmo desenho de `apps/departamentos/omie.py`, com a mesma chave
(`OMIE_APP_KEY` / `OMIE_APP_SECRET`): uma chamada de leitura, fixa no código, e
nada aqui aceita `call` vindo de fora.

O RECORTE

O `ListarCategorias` devolve o plano de contas inteiro: 319 categorias em
14/10/2026, receitas e despesas. Só entram as que ficam sob os prefixos de
`CATEGORIAS_FINANCEIRAS_PREFIXOS` (padrão `1.10`, o grupo "APN" do Omie, com uma
categoria por turma: "APN 107 FLORIANÓPOLIS", "APN 108 FORTALEZA"...). Duas
razões:

- a rota que serve a lista é ANÔNIMA, e o resto do plano de contas não tem por
  que ir para o navegador de qualquer um;
- uma categoria de despesa, ou de outro produto, no select é venda lançada no
  lugar errado.

Turma nova criada no Omie debaixo do grupo aparece sozinha na próxima rodada.
Mudar o grupo é só a variável de ambiente.

Dentro do recorte:

- a categoria **totalizadora** (o próprio grupo) fica de fora: o Omie não aceita
  lançamento nela;
- `conta_inativa` ou `nao_exibir` = "S" conta como inativa. A linha volta a
  valer sozinha se o Omie a reativar.

AS ARMADILHAS DA API

As mesmas dos departamentos: nome de chamada errado ou chave com um caractere a
mais vêm como 500/403 com `faultstring`, e listagem sem registro também vem como
falha. Mais uma daqui: a descrição chega com entidade HTML (`&lt;Disponível&gt;`),
então passa por `html.unescape`.

O QUE O SINCRONIZADOR ESPERA DESTE ARQUIVO

- Todas as categorias do recorte, ativas e inativas, de todas as páginas. Quem não
  vem é desativado no banco.
- Qualquer falha vira `FonteIndisponivel`, com uma mensagem que vai para o log, e
  nunca a credencial. Página que falha no meio derruba a rodada inteira: uma lista
  parcial desativaria o resto.
"""
import html
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

URL = "https://app.omie.com.br/api/v1/geral/categorias/"
REGISTROS_POR_PAGINA = 100
# Rede contra um `total_de_paginas` absurdo. Em 14/10/2026 eram 4 páginas.
MAX_PAGINAS = 20


class FonteIndisponivel(Exception):
    """A fonte não entregou a lista. A mensagem vai para o log."""


@dataclass(frozen=True)
class CategoriaDaFonte:
    codigo: str
    descricao: str
    ativo: bool


def _faultstring(erro: urllib.error.HTTPError) -> str:
    try:
        return json.loads(erro.read().decode("utf-8")).get("faultstring") or "sem detalhe"
    except Exception:
        return "sem detalhe"


def _chama(pagina: int) -> dict:
    corpo = json.dumps(
        {
            "call": "ListarCategorias",
            "param": [{"pagina": pagina, "registros_por_pagina": REGISTROS_POR_PAGINA}],
            "app_key": settings.OMIE_APP_KEY,
            "app_secret": settings.OMIE_APP_SECRET,
        }
    ).encode("utf-8")
    pedido = urllib.request.Request(
        URL, data=corpo, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(pedido, timeout=settings.OMIE_TIMEOUT) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        # Antes do URLError, de quem é subclasse.
        raise FonteIndisponivel(f"Omie respondeu {erro.code}: {_faultstring(erro)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise FonteIndisponivel(f"Sem conexão com o Omie: {erro}") from None
    except ValueError:
        raise FonteIndisponivel("O Omie respondeu algo que não é JSON.") from None


def _prefixos() -> list[str]:
    return [p.strip().rstrip(".") for p in settings.CATEGORIAS_FINANCEIRAS_PREFIXOS.split(",") if p.strip()]


def _no_recorte(codigo: str, prefixos: list[str]) -> bool:
    # "1.10" pega "1.10.93", e não "1.100" nem o próprio "1.10".
    return any(codigo.startswith(prefixo + ".") for prefixo in prefixos)


def listar_categorias() -> list[CategoriaDaFonte]:
    """As categorias do recorte, ativas e inativas, de todas as páginas."""
    if not (settings.OMIE_APP_KEY and settings.OMIE_APP_SECRET):
        raise FonteIndisponivel("OMIE_APP_KEY e OMIE_APP_SECRET não estão configurados.")
    prefixos = _prefixos()
    if not prefixos:
        raise FonteIndisponivel("CATEGORIAS_FINANCEIRAS_PREFIXOS está vazio.")

    categorias: list[CategoriaDaFonte] = []
    vistas = 0
    pagina = 1
    while True:
        dados = _chama(pagina)
        for item in dados.get("categoria_cadastro") or []:
            vistas += 1
            codigo = str(item.get("codigo") or "").strip()
            if not _no_recorte(codigo, prefixos):
                continue
            if str(item.get("totalizadora") or "N").strip().upper() == "S":
                continue
            descricao = html.unescape(str(item.get("descricao") or "")).strip()
            if not descricao:
                logger.warning("Categoria do Omie sem descrição, ignorada: %s", codigo)
                continue
            inativa = str(item.get("conta_inativa") or "N").strip().upper() == "S"
            oculta = str(item.get("nao_exibir") or "N").strip().upper() == "S"
            categorias.append(
                CategoriaDaFonte(codigo=codigo, descricao=descricao[:120], ativo=not (inativa or oculta))
            )
        total = int(dados.get("total_de_paginas") or 1)
        if pagina >= min(total, MAX_PAGINAS):
            break
        pagina += 1

    if vistas and not categorias:
        # O Omie respondeu, mas nada cai no recorte: prefixo errado, ou o grupo
        # foi renumerado lá. Falha com o motivo, em vez do "listagem vazia".
        raise FonteIndisponivel(
            f"Nenhuma das {vistas} categorias do Omie está sob {', '.join(prefixos)} "
            "(CATEGORIAS_FINANCEIRAS_PREFIXOS)."
        )
    return categorias
