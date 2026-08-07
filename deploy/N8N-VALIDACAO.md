# Validação da venda no n8n

Alteração proposta no fluxo **`Elite - Orquestrador`** (`9hJqeQFGo42GwZ6u`).

## Por que

O payload da venda é montado no navegador do consultor e postado **direto** no
webhook público. O webhook não valida nada. Até aqui, o cadeado na tela impedia
o clique, não a venda: bastava abrir o DevTools, mudar `valor_total` e postar.

Nenhuma mudança no Lobby resolve isso sozinha — o código roda na máquina de quem
se quer impedir. O Lobby agora **assina** os valores, mas a assinatura só serve
se alguém a conferir. Esse alguém é o n8n.

**Enquanto esta mudança não for aplicada, a brecha continua aberta.**

## Como o fluxo está hoje

```
Webhook - Recebe Onboarding   (POST, responseMode = responseNode)
  └→ Preparar Dados           (code — lê $input.first().json.body)
     └→ Execution Data
        └→ HTTP Request
           └→ Enriquecer e Classificar Segmento
              └→ Supabase - Criar Registro Onboarding
                 ├→ Expandir Sistemas → Switch → Omie | Conecta | D4Sign | Desconhecido
                 └→ Responder Recebido   (respondToWebhook)
```

Dois detalhes que condicionam a alteração:

1. **`Preparar Dados` lê `$input.first().json.body`** — ou seja, a saída do nó
   imediatamente anterior. Inserir qualquer coisa entre ele e o Webhook faz
   `.body` virar `undefined` e o fluxo quebra **em silêncio**.
2. **O webhook está em `responseNode`.** Todo caminho precisa chegar a um nó de
   resposta. Um ramo sem resposta deixa a requisição pendurada até o timeout, e
   o consultor vê "Falha de conexão ao enviar o cadastro" depois de esperar.

## O que o payload passou a trazer

| Campo | Conteúdo |
|---|---|
| `comprovante` | `"<uuid>.<hmac>"`, ou `null` se o Lobby não respondeu |
| `comprovante_valores` | o bloco exato que foi assinado — repasse como veio |
| `comprovante_erro` | `null`, ou o motivo de não ter comprovante |

## Passo 0 — credencial (não use `$env`)

Nesta instância, acesso a `$env` nas expressões está **bloqueado** (padrão do
n8n 2.26.8, e `N8N_BLOCK_ENV_ACCESS_IN_NODE` não está definido). Use uma
credencial, que é o padrão já adotado aqui (`Conecta Financeiro - x-sync-secret`,
`Easyflow-key`).

Credenciais → novo → **Header Auth**, nome `lobby-n8n-token`:

| Campo | Valor |
|---|---|
| Name | `Authorization` |
| Value | `Bearer <conteúdo de LOBBY_N8N_TOKEN>` |

O token está em `/opt/conecta/env/lobby.env` na prod.solucoes.

## Passo 1 — blindar o `Preparar Dados` (fazer ANTES de inserir qualquer nó)

Primeira linha do nó `Preparar Dados`:

```diff
- const body = $input.first().json.body;
+ const body = $('Webhook - Recebe Onboarding').first().json.body;
```

Referência explícita ao webhook, em vez de "o que veio antes". Passa a não
importar o que se insere no meio — o que é melhor mesmo sem esta alteração.

## Passo 2 — nó HTTP Request "Validar venda"

Entre `Webhook - Recebe Onboarding` e `Preparar Dados`:

| Campo | Valor |
|---|---|
| Method | `POST` |
| URL | `https://lobby.sejaap.com.br/api/venda/validar` |
| Authentication | Generic Credential → Header Auth → `lobby-n8n-token` |
| Send Body | ativado, JSON |
| Body (expressão) | `={{ { comprovante: $json.body.comprovante, valores: $json.body.comprovante_valores } }}` |
| Settings → On Error | **Continue (using regular output)** |

O "On Error: Continue" não é detalhe: se o Lobby estiver fora do ar, o fluxo tem
que seguir para o ramo de conferência, não morrer.

## Passo 3 — nó Switch "Resultado da validação"

Três saídas. A divisão importa: nem toda recusa tem a mesma gravidade.

| # | Saída | Condição | Vai para |
|---|---|---|---|
| 0 | **Aprovada** | `{{ $json.valido }}` é `true` | `Preparar Dados` |
| 1 | **Conferir** | `{{ $json.motivo }}` é `comprovante_ausente` **ou** `comprovante_expirado` **ou** `{{ $json.valido }}` está vazio | `Preparar Dados` **e** nó de notificação |
| 2 | **Bloquear** | qualquer outro caso (fallback) | notificação + `Respond to Webhook` |

`{{ $json.valido }}` vazio significa que o próprio HTTP falhou — o Lobby não
respondeu. É o mesmo caso de `comprovante_ausente`.

### Por que a saída 1 deixa a venda passar

Foi decisão de produto: consultor em campo não pode perder venda porque a
infraestrutura piscou. A venda entra normalmente e alguém confere depois.

### Por que a saída 2 bloqueia

`valores_adulterados`, `assinatura_invalida`, `comprovante_desconhecido` e
`comprovante_ja_usado` **não têm causa inocente**. O payload que chegou não é o
que o servidor assinou. Isso é alerta imediato, não fila.

O ramo precisa terminar num **Respond to Webhook** próprio, senão a requisição
fica pendurada (ver detalhe 2 lá em cima):

```text
respondWith: json
responseBody: ={{ { received: false, motivo: $json.motivo } }}
```

## A tabela de motivos

| `motivo` | Leitura | Ação |
|---|---|---|
| `comprovante_ausente` | O Lobby não respondeu no envio | Conferir o valor e seguir |
| `comprovante_expirado` | Mais de 15 min entre montar e enviar; costuma ser aba esquecida | Conferir e seguir |
| `comprovante_ja_usado` | O mesmo comprovante chegou duas vezes | Bloquear e investigar |
| `valores_adulterados` | **O payload não é o que foi assinado** | Bloquear e alertar |
| `assinatura_invalida` | Comprovante forjado | Bloquear e alertar |
| `comprovante_desconhecido` | Id que nunca foi emitido | Bloquear e alertar |

## Quando `valido` é `true`

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

`autorizado_por` é a resposta confiável para "quem liberou este desconto": vem
do servidor. O `edicao_autorizada` que já existe no payload continua lá, mas é
auditoria montada no cliente — **não confie nele.**

## Como testar sem fechar uma venda de verdade

```bash
# 1. Emite um comprovante (venda a preço de tabela)
curl -s -X POST https://lobby.sejaap.com.br/api/venda/comprovante \
  -H 'Content-Type: application/json' \
  -d '{"fluxo":"elite","categoria_id":"elite","produto_id":"pro",
       "negociado":false,"valor_total":155964,"valor_mensal":12997,
       "entrada":12997,"cronograma":[12997,12997,12997,12997,12997,12997,
       12997,12997,12997,12997,12997],"protocolo":"TESTE-1"}'

# 2. Valida — depois repita a MESMA chamada: tem que vir comprovante_ja_usado
curl -s -X POST https://lobby.sejaap.com.br/api/venda/validar \
  -H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json' \
  -d '{"comprovante":"<COMPROVANTE>","valores":{ ...o mesmo bloco... }}'
```

E tente emitir com valor abaixo da tabela: tem que vir **422**, dizendo que
precisa de autorização de um gerente.

---

## À parte: o webhook da APN não faz nada

O fluxo `lobby-apn` é o **`My workflow 5`** (`4MLj6lPUuDY7TQUd`). Ele está ativo
e tem **exatamente um nó** — o Webhook, sem nenhuma ligação de saída. Pior: o
`httpMethod` não está definido, então vale o padrão **GET**, e o Lobby posta com
**POST**.

Ou seja: **venda de APN enviada pelo Lobby não chega a lugar nenhum hoje.**
Última alteração do fluxo em 31/07, bem antes desta migração — não é regressão
desta entrega.

Não faz sentido acrescentar validação num fluxo que ainda não existe. O
comprovante da APN já é emitido pelo Lobby e vai no payload; quando o fluxo for
construído, ele valida do mesmo jeito descrito acima, trocando o nome do nó de
webhook nas expressões.
