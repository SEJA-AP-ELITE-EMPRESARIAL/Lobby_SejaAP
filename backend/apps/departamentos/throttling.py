"""
Limite da leitura anônima dos departamentos.

Mesmo aviso do `apps/catalogo/throttling.py`: sem cache compartilhado, o teto é
por processo do gunicorn e zera a cada deploy. É contenção de rajada.
"""
from rest_framework.throttling import AnonRateThrottle


class DepartamentosPublicoThrottle(AnonRateThrottle):
    """Folgado: o front reconsulta a cada 5 minutos e ao voltar para a aba, e
    vários consultores dividem o IP do escritório."""

    scope = "departamentos_publico"
