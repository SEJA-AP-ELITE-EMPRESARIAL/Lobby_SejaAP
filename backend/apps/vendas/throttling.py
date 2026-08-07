"""
Limite da emissão de comprovante.

Rota anônima: qualquer consultor emite ao fechar uma venda. Teto folgado porque
uma venda pode ser reenviada algumas vezes (erro de rede, correção de cadastro),
mas existe para que ninguém use o endpoint como oráculo — testando valores até
descobrir qual passa na conferência contra a tabela.
"""
from rest_framework.throttling import AnonRateThrottle


class ComprovanteThrottle(AnonRateThrottle):
    scope = "comprovante"
