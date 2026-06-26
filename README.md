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
| **`Lobby de Produtos.html`** | **Página funcional** — é o que vai para produção. Autossuficiente (React 18 + Babel via CDN, tokens e logo embutidos). |
| `Lobby de Produtos.dc.html` | Fonte de design (protótipo no framework DC). Apenas referência visual. |
| `_ds/` | Design system Conecta AP (tokens de cor/tipografia). Referência. |
| `assets/logo-sejaap.svg` | Logo (também já embutido inline na página funcional). |
| `support.js` | Runtime do protótipo DC (só usado pelo `.dc.html`). |

## Como rodar

A página é um único HTML estático. Basta servir a pasta e abrir o arquivo:

```bash
# qualquer servidor estático serve; ex.:
npx serve .
# depois abra "Lobby de Produtos.html"
```

Abrir via `file://` também funciona, mas servir por HTTP é recomendado para
evitar bloqueios de CORS/cache do navegador.

## Integrações (webhooks n8n)

A página reutiliza, sem alterar o contrato, os webhooks da página de
pré-contrato (constantes no topo do `<script>` em `Lobby de Produtos.html`):

- **CNPJ** — `POST https://n8n.sejaap.com.br/webhook/brasilapi-cnpj` → `{ cnpj, cnpj_formatado }`
- **CEP** — `POST https://n8n.sejaap.com.br/webhook/busca-cep` → `{ cep, cep_formatado }`
- **Cadastro** — `POST https://n8n.sejaap.com.br/webhook/cadastro-cliente-elite` → payload com `empresa`, `representante`, `produto`, `pagamento`, `destino`, `aceites`, `metadata`. Erros de negócio voltam em `faultstring`.

> Os webhooks precisam responder com cabeçalhos **CORS** para a chamada do
> navegador funcionar.

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

A página de pré-contrato roda no Cloudflare Worker `pre-registro`
(`pre-registro.seja-alta-performance.workers.dev`). Este lobby pode ser servido
pelo mesmo worker (ex.: rota `/lobby`) ou por qualquer hospedagem estática.
