# Validação da venda no n8n

O que muda nos fluxos `onboarding-cliente-elite` e `lobby-apn`.

## Por que

O payload da venda é montado no navegador do consultor e postado **direto** no
webhook público. O webhook não valida nada. Até aqui, o cadeado na tela impedia o
clique, não a venda: bastava abrir o DevTools, mudar `valor_total` e postar.

Nenhuma mudança no Lobby resolve isso sozinha — o código roda na máquina de quem
se quer impedir. O Lobby agora **assina** os valores, mas a assinatura só serve
se alguém a conferir. Esse alguém é o n8n.

**Enquanto esta mudança não for aplicada, a brecha continua aberta.**

## O que o payload passou a trazer

Três campos novos, no primeiro nível:

| Campo | Conteúdo |
|---|---|
| `comprovante` | `"<uuid>.<hmac>"`, ou `null` se o Lobby não respondeu |
| `comprovante_valores` | o bloco exato que foi assinado — repasse como veio |
| `comprovante_erro` | `null`, ou o motivo de não ter comprovante |

`comprovante_valores` existe para você não precisar remontar nada: mande de
volta o objeto inteiro, sem tocar.

## O que acrescentar no fluxo

Logo depois do nó de Webhook, **antes de qualquer coisa que grave ou envie**:

### 1. Nó HTTP Request — "Validar venda"

```
Método:  POST
URL:     https://lobby.sejaap.com.br/api/venda/validar
Headers: Authorization: Bearer {{ $env.LOBBY_N8N_TOKEN }}
         Content-Type: application/json
Body (JSON):
{
  "comprovante": "={{ $json.body.comprovante }}",
  "valores": "={{ $json.body.comprovante_valores }}"
}
```

Marque **"Continue On Fail"**. Se o Lobby estiver fora do ar, o fluxo tem que
seguir para o ramo de conferência, não morrer.

### 2. Nó IF — "Venda conferida?"

Condição verdadeira quando: `{{ $json.valido }}` é igual a `true`.

- **Verdadeiro** → segue o fluxo normal, como hoje.
- **Falso** → ramo de conferência (abaixo).

### 3. O ramo falso

Não descarte a venda. Ela pode ser legítima — o Lobby pode ter ficado
indisponível no instante do envio. Encaminhe para conferência humana:

- notifique o canal do time (Telegram/e-mail), com `protocolo`, `motivo` e
  `comprovante_erro`;
- grave em algum lugar consultável (planilha, base do n8n, o que já se usa);
- **não** crie contrato nem cadastro automático.

### Os motivos, e o que cada um significa

| `motivo` | Leitura |
|---|---|
| `comprovante_ausente` | O Lobby não respondeu no envio. Provavelmente legítima — confira o valor e siga. |
| `comprovante_expirado` | Passaram mais de 15 minutos entre montar e enviar. Costuma ser aba esquecida aberta. |
| `comprovante_ja_usado` | O mesmo comprovante chegou duas vezes. Ou o n8n reprocessou, ou é replay. |
| `valores_adulterados` | **O payload que chegou não é o que foi assinado.** Não existe causa inocente para isto. |
| `assinatura_invalida` | Comprovante forjado. Idem. |
| `comprovante_desconhecido` | Id que nunca foi emitido. Idem. |

Os três últimos merecem alerta imediato, não fila.

### Quando `valido` é `true`

A resposta traz o contexto para auditoria:

```json
{
  "valido": true,
  "protocolo": "PRO-260806012345",
  "fluxo": "elite",
  "negociado": true,
  "autorizado_por": "gerente@sejaap.com.br",
  "papel": "gerente",
  "emitido_em": "2026-08-07T11:20:00-03:00"
}
```

`autorizado_por` é a resposta confiável para "quem liberou este desconto" — ela
vem do servidor, não do navegador. O `edicao_autorizada` que já existia no
payload continua lá, mas é auditoria montada no cliente: **não confie nele.**

## O segredo

`LOBBY_N8N_TOKEN` precisa ser o mesmo dos dois lados:

- no Lobby: `/opt/conecta/env/lobby.env` na prod.solucoes
- no n8n: variável de ambiente do container

Se o Lobby não tiver o token configurado, `/api/venda/validar` responde **503** —
e o fluxo cai no ramo de conferência. Falha visível, nunca aceitação silenciosa.

## Como testar sem fechar uma venda de verdade

Peça um comprovante à mão e valide-o:

```bash
# 1. Emite (venda a preço de tabela, sem negociação)
curl -s -X POST https://lobby.sejaap.com.br/api/venda/comprovante \
  -H 'Content-Type: application/json' \
  -d '{"fluxo":"elite","categoria_id":"elite","produto_id":"pro",
       "negociado":false,"valor_total":155964,"valor_mensal":12997,
       "entrada":12997,"cronograma":[12997,12997,12997,12997,12997,12997,
       12997,12997,12997,12997,12997],"protocolo":"TESTE-1"}'

# 2. Valida (troque <TOKEN> e <COMPROVANTE>)
curl -s -X POST https://lobby.sejaap.com.br/api/venda/validar \
  -H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json' \
  -d '{"comprovante":"<COMPROVANTE>","valores":{ ...o mesmo bloco... }}'
```

A segunda chamada com o mesmo comprovante tem que devolver
`comprovante_ja_usado` — é assim que se confirma que o uso único funciona.

E tente emitir um comprovante com valor abaixo da tabela: tem que vir **422**,
com a mensagem explicando que é preciso autorização de um gerente.
