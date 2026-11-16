"""
Limite da leitura dos valores do DH - Avaliação.

Por pessoa desde a TSK-1513, quando a rota deixou de ser anônima; o nome e o
`scope` ficaram os de antes. Mesmo aviso do `apps/catalogo/throttling.py`: sem
cache compartilhado, o teto é por processo do gunicorn e zera a cada deploy.
"""
from apps.contas.throttling import PorPessoaThrottle


class ValoresPublicoThrottle(PorPessoaThrottle):
    """O ritmo dos departamentos: o lobby reconsulta a cada 5 minutos, ao voltar
    para a aba e antes de enviar."""

    scope = "dh_avaliacao_publico"
