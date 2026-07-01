# Geração do Protocolo da Venda

> Documento de referência sobre o **código de protocolo** gerado ao concluir uma
> venda no Lobby Conecta AP. Toda a lógica vive em [`index.html`](../index.html).

## O que é

Ao concluir uma venda, a página gera um **código único** (o "protocolo") que:

1. é **exibido** na tela de sucesso, sob o rótulo **Protocolo**; e
2. é **enviado ao webhook** de cadastro (`onboarding-cliente-elite`) dentro do
   payload, no campo **`protocolo`**.

Antes ele era apenas visual (`CAP-2026-######`); agora ele carrega informação
estruturada sobre a venda e é persistido no fluxo n8n.

## Formato

```
SSS-YYMMDDPRRRRR
└┬┘ └──┬──┘│└─┬─┘
 │     │   │  └── RRRRR : 5 dígitos aleatórios
 │     │   └───── P     : 1 dígito da forma de pagamento da ENTRADA
 │     └───────── YYMMDD: data da venda (ano/mês/dia, 2 dígitos cada)
 └─────────────── SSS   : sigla de 3 letras do produto vendido
```

- **Tamanho fixo:** 16 caracteres (`3` + `-` + `6` + `1` + `5`).
- **Único separador:** o hífen (`-`), logo após a sigla do produto.
- **Exemplo:** `PRO-260701005821` → ELITE PRO, vendido em **01/07/2026**, entrada
  no **Pix** (`0`), sufixo aleatório `05821`.

### 1. `SSS` — sigla do produto (3 letras)

Cada produto tem uma `sigla` de 3 letras definida no catálogo (`CATS`). Hoje só a
categoria **Elite** está ativa:

| Produto        | Sigla |
|----------------|:-----:|
| ELITE PRO      | `PRO` |
| ELITE GESTÃO   | `GES` |
| ELITE EVO      | `EVO` |

Categorias travadas (já preparadas para quando forem liberadas):

| Produto                     | Sigla | Produto           | Sigla |
|-----------------------------|:-----:|-------------------|:-----:|
| Imersão Gestão 360          | `IMG` | APN Starter       | `AST` |
| Trilha Líder Elite          | `TLE` | APN Pro           | `APR` |
| Vendas de Alta Performance  | `VAP` | APN Black         | `ABL` |
| Palestra In Company         | `PIC` | Palestra Magna    | `PMA` |
| Ciclo de Palestras Anual    | `CPA` |                   |       |

### 2. `YYMMDD` — data da venda

Data em que a venda foi concluída (`new Date()` no momento do envio), no fuso
**local** do navegador do consultor:

- `YY` = ano com 2 dígitos (`2026` → `26`)
- `MM` = mês com 2 dígitos (`07`)
- `DD` = dia com 2 dígitos (`01`)

### 3. `P` — dígito da forma de pagamento da entrada

Um único dígito que representa a forma escolhida para pagar a **entrada**
(`entradaForma`). O mapeamento vive em `PAYMENT_METHODS` (campo `code`):

| Forma de pagamento   | Dígito |
|----------------------|:------:|
| Pix                  | `0`    |
| Cartão de crédito    | `1`    |
| Cartão de débito     | `2`    |
| Boleto               | `3`    |
| Permuta              | `4`    |
| Link de pagamento    | `5`    |
| Recorrência          | `6`    |

> Formas `7`, `8` e `9` ficam livres. `9` é usado como **fallback** para uma
> forma desconhecida (não deve acontecer pela UI, que restringe às opções acima).

### 4. `RRRRR` — 5 dígitos aleatórios

Sufixo aleatório para tornar o protocolo único mesmo em vendas do mesmo produto,
na mesma data e com a mesma forma de pagamento. Gerado com `Math.random()`
(uso não-criptográfico), sempre com 5 dígitos (zeros à esquerda preservados —
`00042`).

## Onde fica no código (`index.html`)

| Trecho | Linhas (aprox.) | Papel |
|---|---|---|
| `PAYMENT_METHODS` (campo `code`) | ~231 | Dígito de cada forma de pagamento |
| `paymentCode(id)` | ~242 | Resolve o dígito da forma (fallback `9`) |
| `buildProtocol(product, entradaForma, date)` | ~252 | Monta o protocolo completo |
| `CATS` (campo `sigla` por produto) | ~470 | Sigla de 3 letras de cada produto |
| `finalize()` → `payload.protocolo` | ~824 | Gera **antes** do envio, injeta no payload e usa como protocolo exibido |
| Tela de sucesso (`{s.protocol}`) | ~1505 | Exibe o protocolo sob o rótulo **Protocolo** |

### A função geradora

```js
function buildProtocol(product, entradaForma, date) {
  const d = date instanceof Date ? date : new Date();
  const sigla = ((product && product.sigla) || 'XXX')
    .toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3).padEnd(3, 'X');
  const yymmdd = pad2(d.getFullYear() % 100) + pad2(d.getMonth() + 1) + pad2(d.getDate());
  const rand = String(Math.floor(Math.random() * 100000)).padStart(5, '0');
  return `${sigla}-${yymmdd}${paymentCode(entradaForma)}${rand}`;
}
```

- A sigla é **sanitizada**: só letras, no máximo 3, completada com `X` se faltar
  (produto sem `sigla` vira `XXX`).
- O protocolo é gerado **uma vez** em `finalize()`, **antes** do `fetch`, para
  que o mesmo valor vá ao webhook **e** apareça na tela.

## Fluxo de envio

```
finalize()
  ├─ protocolo = buildProtocol(product, entradaForma, hoje)
  ├─ payload   = buildPayload()          // empresa, representante, produto, ...
  ├─ payload.protocolo = protocolo       // injeta o código no payload
  ├─ POST onboarding-cliente-elite       // envia ao webhook n8n
  └─ tela de sucesso mostra `protocolo`
```

O payload enviado ao webhook passa a incluir, no topo:

```jsonc
{
  "protocolo": "PRO-260701005821",
  "empresa": { /* ... */ },
  "representante": { /* ... */ },
  "produto": { /* ... */ },
  "pagamento": { /* ... */ },
  "destino": { /* ... */ },
  "aceites": { /* ... */ },
  "metadata": { /* ... */ }
}
```

## Como manter

- **Nova sigla de produto:** adicione/edite o campo `sigla` do produto em `CATS`
  (`index.html`, ~470). Use 3 letras maiúsculas; evite colisões entre produtos.
- **Novo dígito de pagamento:** adicione a forma em `PAYMENT_METHODS` com um
  `code` **único** (próximos livres: `7`, `8`). Reserve `9` para desconhecido.
- **Mudar o formato:** ajuste `buildProtocol()`. Se o tamanho/estrutura mudar,
  atualize também os consumidores no n8n e este documento.

## Exemplos

| Produto      | Data       | Entrada           | Protocolo          |
|--------------|------------|-------------------|--------------------|
| ELITE PRO    | 01/07/2026 | Pix (`0`)         | `PRO-260701005821` * |
| ELITE GESTÃO | 15/12/2026 | Cartão crédito(`1`)| `GES-261215112345` * |
| ELITE EVO    | 03/01/2027 | Recorrência (`6`) | `EVO-270103698040` * |

\* Os 5 últimos dígitos são aleatórios — variam a cada venda.
