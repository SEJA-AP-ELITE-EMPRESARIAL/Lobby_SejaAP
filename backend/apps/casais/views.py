"""
`POST /api/casais/elite` — o CNPJ do contratante é cliente ativo do Elite? (TSK-1622)

A tela do Casais de Alta Performance pergunta quando o contratante é PJ e o CNPJ
fica completo, para mostrar o valor que vale — sem o consultor poder trocar. Quem
pergunta ao Conecta é o servidor (`servicos.conferir_cnpj`), com a chave da
aplicação `lobby`; o navegador nunca fala com o Conecta.

Corpo: `{"cnpj": "11.222.333/0001-81"}`. POST, e não GET: o CNPJ fica fora da URL
e, com ela, dos access logs.

Resposta 200, sempre que o CNPJ vale — com o Conecta respondendo ou não:

    { "cliente_ativo": true | false | null,   null = o Conecta não respondeu
      "conferido": bool,                      a resposta veio do Conecta
      "tabela": "elite" | "cheio",            o valor que vale (sem resposta: o cheio,
                                              até o consultor marcar a caixa)
      "valor": 35000 | 39997,
      "consultor_decide": bool,               sem resposta, a tela mostra a caixa
                                              "paga o valor do Elite Empresarial" (TSK-1640)
      "motivo": null | "indisponivel" | "limite" | "manutencao" | "limite_da_pessoa" | ...,
      "tentar_em": null | segundos,
      "mensagem": "a frase da tela" }

400 para o CNPJ que não é CNPJ (não chega ao Conecta: a malformada também gasta o
balde de lá); 401 sem a sessão; 429 do teto da rota, por pessoa.

Esta resposta é só o que a tela MOSTRA. A conferência que vale é a do comprovante
(`apps.vendas.servicos`), que pergunta de novo — pela resposta guardada aqui ou
ao Conecta — e só assina o valor do Elite numa PJ que o Conecta disse ser ativa
ou, marcado à mão, se ele também ficar sem resposta para o servidor.
"""
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalogo.serializers import numero
from apps.contas.autenticacao import JWTRevalidado

from .servicos import VALOR_DA_TABELA, conferir_cnpj, mensagem, normaliza_cnpj
from .throttling import EliteThrottle


@api_view(["POST"])
# Declarada aqui, e não herdada do padrão, como a da presença: a rota gasta um
# balde externo, e só a sessão da tela de quem vende pode gastá-lo.
@authentication_classes([JWTRevalidado])
@permission_classes([IsAuthenticated])
@throttle_classes([EliteThrottle])
def elite(request):
    dados = request.data if isinstance(request.data, dict) else {}
    cnpj = normaliza_cnpj(dados.get("cnpj"))
    if cnpj is None:
        return Response({"erro": "CNPJ inválido."}, status=status.HTTP_400_BAD_REQUEST)

    conferencia = conferir_cnpj(cnpj, pessoa=request.user)
    resposta = Response({
        "cliente_ativo": conferencia.cliente_ativo,
        "conferido": conferencia.conferido,
        "tabela": conferencia.tabela,
        "valor": numero(VALOR_DA_TABELA[conferencia.tabela]),
        "consultor_decide": conferencia.consultor_decide,
        "motivo": conferencia.motivo or None,
        "tentar_em": conferencia.tentar_em,
        "mensagem": mensagem(conferencia),
    })
    # Atrás da Cloudflare, uma resposta cacheada na borda seria o status de
    # outro dia — ou de outro consultor.
    resposta["Cache-Control"] = "no-store"
    return resposta
