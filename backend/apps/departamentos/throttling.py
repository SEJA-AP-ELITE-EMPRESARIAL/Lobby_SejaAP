"""
Limite da leitura dos departamentos.

Por pessoa desde a TSK-1513, quando a rota deixou de ser anônima; o nome e o
`scope` ficaram os de antes. Mesmo aviso do `apps/catalogo/throttling.py`: sem
cache compartilhado, o teto é por processo do gunicorn e zera a cada deploy.
"""
from apps.contas.throttling import PorPessoaThrottle


class DepartamentosPublicoThrottle(PorPessoaThrottle):
    """Folgado: o front reconsulta a cada 5 minutos e ao voltar para a aba."""

    scope = "departamentos_publico"
