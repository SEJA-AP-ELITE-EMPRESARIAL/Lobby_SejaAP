"""
Limite da leitura anônima dos valores do DH - Avaliação.

Mesmo aviso do `apps/catalogo/throttling.py`: sem cache compartilhado, o teto é
por processo do gunicorn e zera a cada deploy. É contenção de rajada.
"""
from rest_framework.throttling import AnonRateThrottle


class ValoresPublicoThrottle(AnonRateThrottle):
    """O ritmo dos departamentos: o lobby reconsulta a cada 5 minutos, ao voltar
    para a aba e antes de enviar, e vários consultores dividem o IP do escritório."""

    scope = "dh_avaliacao_publico"
