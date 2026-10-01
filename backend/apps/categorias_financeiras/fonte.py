"""
De onde vêm as categorias financeiras. AINDA NÃO LIGADO (TSK-877).

A API vai ser passada pelo Victor. Até lá, `listar_categorias` só levanta
`FonteIndisponivel`, e o resto do app funciona em cima disso: o laço loga a
falha a cada rodada, o banco fica sem categoria nenhuma e, sem nenhuma
sincronização boa, o lobby nem mostra o campo (ver `servicos.estado_publico`).

O CONTRATO QUE O CLIENTE DA API TEM DE CUMPRIR

- Devolver TODAS as categorias, ativas e inativas, de todas as páginas. Quem não
  vem na listagem é desativado no banco.
- Qualquer falha (fora do ar, credencial errada, resposta que não é a esperada)
  vira `FonteIndisponivel`, com uma mensagem que vai para o log. Nunca a
  credencial na mensagem.
- Falha nunca vira lista vazia: o `sincronizar` já recusa a vazia, mas uma lista
  PARCIAL desativaria o resto. Página que falhou no meio é falha da rodada.
- Credencial, se houver, vem do `.env` pelo `settings`, nunca do repo.

O modelo é `apps/departamentos/omie.py`. Se a API for o `ListarCategorias` do
Omie, a chave é a mesma dos departamentos.
"""
from dataclasses import dataclass


class FonteIndisponivel(Exception):
    """A fonte não entregou a lista. A mensagem vai para o log."""


@dataclass(frozen=True)
class CategoriaDaFonte:
    codigo: str
    descricao: str
    ativo: bool


def listar_categorias() -> list[CategoriaDaFonte]:
    """Todas as categorias da fonte, ativas e inativas."""
    raise FonteIndisponivel("A API das categorias financeiras ainda não foi ligada ao Lobby.")
