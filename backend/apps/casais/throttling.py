"""
Limite da rota que confere o CNPJ no Conecta (TSK-1622), por pessoa.

Teto da ROTA, folgado (`LOBBY_RATE_CASAIS_ELITE`, 60 por hora): a tela pergunta
uma vez por CNPJ por venda, e de novo só no "Conferir de novo" ou depois de o
comprovante recusar. O que protege o balde do Conecta, que é um só para o Lobby
inteiro, é outro teto, o das perguntas que vão de fato até lá
(`LOBBY_CASAIS_CONSULTAS_POR_HORA`, em `servicos.py`): a resposta guardada não
gasta nada dele.
"""
from apps.contas.throttling import PorPessoaThrottle


class EliteThrottle(PorPessoaThrottle):
    scope = "casais_elite"
