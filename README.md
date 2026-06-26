# Lobby SejaAP — Conecta AP

Lobby interno de produtos do consultor SejaAP. A partir de uma grade de
categorias, o consultor abre um fluxo de cadastro e contratação do cliente
(Produto → Empresa → Pagamento → Resumo) que reutiliza os **webhooks n8n**
reais da página de pré-contrato.

> Hoje a única categoria ativa é **Elite**. As demais (Treinamentos, APN,
> Palestras) aparecem como **"Em implementação"** (travadas) até serem liberadas.

## Arquivos

| Arquivo | Descrição |
|---|---|
| **`index.html`** | **Página funcional** — é o que vai para produção (servida na raiz). Autossuficiente (React 18 + Babel via CDN, tokens e logo embutidos). |
| `Lobby de Produtos.dc.html` | Fonte de design (protótipo no framework DC). Apenas referência visual. |
| `_ds/` | Design system Conecta AP (tokens de cor/tipografia). Referência. |
| `assets/logo-sejaap.svg` | Logo (também já embutido inline na página funcional). |
| `support.js` | Runtime do protótipo DC (só usado pelo `.dc.html`). |

## Como rodar

A página é um único HTML estático. Basta servir a pasta e abrir o arquivo:

```bash
# qualquer servidor estático serve; ex.:
npx serve .
# abra http://localhost:3000 — index.html é servido na raiz
```

Abrir via `file://` também funciona, mas servir por HTTP é recomendado para
evitar bloqueios de CORS/cache do navegador.

## Integrações (webhooks n8n)

A página reutiliza, sem alterar o contrato, os webhooks da página de
pré-contrato (constantes no topo do `<script>` em `index.html`):

- **CNPJ** — `POST https://n8n.sejaap.com.br/webhook/brasilapi-cnpj` → `{ cnpj, cnpj_formatado }`
- **CEP** — `POST https://n8n.sejaap.com.br/webhook/busca-cep` → `{ cep, cep_formatado }`
- **Cadastro** — `POST https://n8n.sejaap.com.br/webhook/onboarding-cliente-elite` → payload com `empresa`, `representante`, `produto`, `pagamento`, `destino`, `aceites`, `metadata`. Erros de negócio voltam em `faultstring`.
- **Pix (entrada)** — o front chama `/api/pix` (proxy, ver abaixo), que repassa para
  `https://n8n.sejaap.com.br/webhook/907bbfb8-…`. Contrato esperado:
  - `{ acao: "gerar", valor, valor_centavos, cnpj, razao_social, email, telefone, endereco{…} }` → responde **na hora** com `{ orderId|txid, pix_copia_cola | qr_base64 | qr_url }` (sem segurar a conexão).
  - `{ acao: "status", orderId|txid }` → `{ status: "pendente" }` … `{ status: "pago" }`.

> Os webhooks de CNPJ/CEP/Cadastro precisam responder com cabeçalhos **CORS** para
> a chamada do navegador funcionar.

### Proxy Pix (`/api/pix`) e o segredo `EASYFLOW_KEY`

O webhook Pix exige o header de autenticação **`easyflow-key`**, que **não pode** ir
pelo navegador (ficaria público). Por isso o front chama `/api/pix` — uma
**Cloudflare Pages Function** (`functions/api/pix.js`) que injeta esse header no
**servidor**, a partir de um segredo, e repassa ao n8n.

Configurar o segredo (uma vez): **Cloudflare → Pages → (projeto) → Settings →
Variables and secrets → Add**, tipo **Secret**, Name `EASYFLOW_KEY`, Value = a chave.

## Personalização rápida

- **Catálogo de produtos/preços:** array `CATS` no `<script>`. Cada item tem
  `{ id, name, desc, duration, price, icon, ... }`. Para liberar uma categoria,
  remova `locked: true` dela.
- **Valor por evento/negociação:** na etapa *Produto*, o botão **"Editar valor"**
  libera ajustar a mensalidade (recorrentes) ou o valor à vista (eventos); o
  total e o cronograma recalculam automaticamente.
- **Pagamento:** na etapa *Pagamento*, o botão **"Editar valores"** libera
  personalizar entrada, número de parcelas, valores, datas e formas, com o total
  como âncora (a soma sempre fecha com o total).

## Deploy

Publicado via **Cloudflare Pages** conectado a este repositório:

- Build command: *(vazio)* · Output directory: `/`
- Cada `push` na branch `main` gera um deploy automático.
- Domínio de produção: **https://lobby.sejaap.com.br** (custom domain no Pages;
  DNS + SSL gerenciados pelo Cloudflare, já que `sejaap.com.br` está na conta).
