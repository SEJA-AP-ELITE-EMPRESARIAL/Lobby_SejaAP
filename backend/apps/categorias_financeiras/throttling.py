"""
Limite da leitura das categorias financeiras. Mesmo aviso do
`apps/departamentos/throttling.py`: por pessoa desde a TSK-1513, com o `scope`
de quando a rota era anônima.
"""
from apps.contas.throttling import PorPessoaThrottle


class CategoriasFinanceirasPublicoThrottle(PorPessoaThrottle):
    """Folgado pelo mesmo motivo do departamento: o front reconsulta a cada 5
    minutos e ao voltar para a aba."""

    scope = "categorias_financeiras_publico"
