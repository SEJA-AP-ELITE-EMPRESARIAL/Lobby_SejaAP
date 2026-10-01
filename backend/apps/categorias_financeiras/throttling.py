"""
Limite da leitura anônima das categorias financeiras. Mesmo aviso do
`apps/departamentos/throttling.py`: é por processo do gunicorn, contenção de
rajada.
"""
from rest_framework.throttling import AnonRateThrottle


class CategoriasFinanceirasPublicoThrottle(AnonRateThrottle):
    """Folgado pelo mesmo motivo do departamento: o front reconsulta a cada 5
    minutos e ao voltar para a aba, e vários consultores dividem o IP."""

    scope = "categorias_financeiras_publico"
