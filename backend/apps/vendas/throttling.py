"""
Limite da emissão de comprovante.

Teto folgado porque uma venda pode ser reenviada algumas vezes (erro de rede,
correção de cadastro), mas existe para que ninguém use o endpoint como oráculo
— testando valores até descobrir qual passa na conferência contra a tabela.

Por pessoa desde a TSK-1513: a rota passou a exigir a sessão de quem vende, e o
balde é o dela, não o do IP nem o de quem autorizou a negociação.
"""
from apps.contas.throttling import PorPessoaThrottle


class ComprovanteThrottle(PorPessoaThrottle):
    scope = "comprovante"
