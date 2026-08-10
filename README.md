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
| **`index.html`** | **Página funcional** — o lobby do consultor. Autossuficiente (React 18 + Babel via CDN, tokens e logo embutidos). Não tem build. |
| **`admin.html`** | **Painel de valores de tabela** (`/admin`) — onde a diretoria muda os preços sem deploy. |
| **`backend/`** | API em Django: o catálogo, a publicação da tabela e a autorização de negociação. |
| **`deploy/`** | Dockerfiles do nginx e do túnel do banco, mais o vhost do host. |
| **`docker-compose.yml`** | A stack como ela roda na `prod.solucoes`. |
| ~~`functions/`~~ | **Removida.** Era a implementação anterior em Cloudflare Pages Functions, com senha compartilhada e catálogo em KV. Nunca foi ao ar. Ficava no repo como referência, mas a documentação passou a mandar editar a APN lá — então virou armadilha. Está no histórico: `git show 338e932 -- functions/`. |
| `Lobby de Produtos.dc.html` | Fonte de design (protótipo no framework DC). Apenas referência visual. |
| `_ds/` | Design system Conecta AP (tokens de cor/tipografia). Referência. |
| `assets/logo-sejaap.svg` | Logo (também já embutido inline na página funcional). |
| `support.js` | Runtime do protótipo DC (só usado pelo `.dc.html`). |

## O lobby é anônimo — a negociação não

Esta é a regra que atravessa o projeto inteiro:

> **Qualquer consultor abre, navega, cota, cadastra e envia a venda sem nenhuma
> credencial.** Não existe tela de login no lobby, e nenhuma rota do fluxo de
> cadastro pode passar a exigir uma.

O que exige credencial é **negociar** — alterar valor de produto ou cronograma de
parcelas — e **publicar a tabela de preços**. São coisas diferentes, com pessoas
diferentes e mecanismos diferentes:

|   | O que é | Quem pode | Onde | Como a credencial funciona |
|---|---|---|---|---|
| **Valor de tabela** | O preço padrão que todo consultor vê. | **Diretoria** | `/admin` | Sessão normal, no computador de quem publica. |
| **Valor negociado** | O desconto de UMA venda. Não altera a tabela. | **Gerente** ou diretoria | No próprio lobby | Autorização pontual, **válida só para aquela venda**. |

### Por que a autorização de negociação não é sessão

O gerente digita a credencial **no aparelho do consultor**, no meio de um
atendimento. Uma sessão de horas significaria que a primeira autorização do dia
libera todas as vendas seguintes naquele tablet, sem ninguém perceber.

Por isso ela vive só na memória da página e morre ao: enviar o cadastro, trocar
de produto, voltar ao lobby, ou clicar em "Encerrar autorização". Recarregar a
página também encerra — não há nada no `sessionStorage`.

### Quem confere a senha

O **[Conecta ID](https://github.com/SEJA-AP-ELITE-EMPRESARIAL/conecta-id)**, o
serviço de identidade da empresa — o mesmo login do kanban, do CRM e do
financeiro. Nenhuma senha mora neste repositório nem chega ao navegador.

Os papéis (`gerente` / `diretoria`) são **deste app**, não do Conecta ID: lá o
acesso é binário, e quem guarda permissão de negócio é cada sistema. Promover
alguém:

```bash
docker exec lobby-backend python manage.py promover_no_lobby \
    fulano@sejaap.com.br --papel diretoria
```

> **Não edite mais preços no `index.html`.** O array `CATS` lá continua existindo,
> mas é só uma **rede de segurança**: se a API estiver fora do ar, o lobby abre com
> aqueles valores em vez de quebrar no meio de um evento. A fonte de verdade é o banco.

### Como o catálogo chega na página

```text
index.html  --GET /api/catalogo-->  Postgres `lobby` (db-sejaap, via túnel SSH)
   (anônimo)                                   ^
                                               |  PUT /api/catalogo (exige diretoria)
                                        admin.html  (/admin)
```

Cada publicação grava uma linha em `PublicacaoCatalogo` com o catálogo inteiro, o
resumo do que mudou e **quem publicou** — algo que a implementação anterior, em KV,
nunca teve.

## Como rodar

```bash
cd backend
python -m venv .venv && ./.venv/Scripts/pip install -r requirements.txt   # Linux: .venv/bin/pip
./.venv/Scripts/python manage.py migrate      # cria o banco e semeia o catálogo
./.venv/Scripts/python manage.py test apps    # a suíte inteira
./.venv/Scripts/python manage.py runserver
```

Sem `.env`, sobe com SQLite e `DEBUG=true` — o suficiente para mexer em tudo menos
no login, que precisa de um Conecta ID alcançável (`AUTH_CENTRAL_ATIVO`).

A stack completa, igual à de produção:

```bash
cp .env.example .env      # e preencha banco + IDENTIDADE_APP_KEY
docker compose up -d --build
# lobby: http://127.0.0.1:8095   ·   painel: http://127.0.0.1:8095/admin
```

Servir só o HTML (`npx serve .`) continua funcionando para mexer em layout: o lobby
cai no catálogo embutido e a edição de valores fica indisponível.

## Integrações (webhooks n8n)

A página reutiliza, sem alterar o contrato, os webhooks da página de
pré-contrato (constantes no topo do `<script>` em `index.html`):

- **CNPJ** — `POST https://n8n.sejaap.com.br/webhook/brasilapi-cnpj` → `{ cnpj, cnpj_formatado }`
- **CEP** — `POST https://n8n.sejaap.com.br/webhook/busca-cep` → `{ cep, cep_formatado }`
- **Cadastro** — `POST https://n8n.sejaap.com.br/webhook/onboarding-cliente-elite` → payload com `protocolo`, `empresa`, `representante`, `produto`, `pagamento`, `destino`, `aceites`, `metadata`. Erros de negócio voltam em `faultstring`.
  - `protocolo` — código da venda gerado no cliente e exibido como **Protocolo** na tela de sucesso. Formato `SSS-YYMMDDPRRRRR`: `SSS` = sigla de 3 letras do produto (`PRO`/`GES`/`EVO`…), `YYMMDD` = data da venda, `P` = dígito da forma de pagamento da entrada (Pix=0, cartão crédito=1, cartão débito=2, boleto=3, permuta=4, link=5, recorrência=6), `RRRRR` = 5 dígitos aleatórios. Ex.: `PRO-260701005821`.
- **Pix (entrada)** — `POST https://n8n.sejaap.com.br/webhook/907bbfb8-…`. Contrato esperado:
  - `{ acao: "gerar", valor, valor_centavos, cnpj, razao_social, email, telefone, endereco{…} }` → responde **na hora** com `{ orderId|txid, pix_copia_cola | qr_base64 | qr_url }` (sem segurar a conexão).
  - `{ acao: "status", orderId|txid }` → `{ status: "pendente" }` … `{ status: "pago" }`.

> Os webhooks precisam responder com cabeçalhos **CORS** para a chamada do navegador funcionar.

## Personalização rápida

- **Preços:** em **`/admin`**, com a credencial da diretoria. Não mexa no `CATS` do
  `index.html` (veja *O lobby é anônimo — a negociação não*, acima).
- **Produtos e categorias novas:** pelo **`/django-admin/`**, sem deploy. Crie a
  categoria, os produtos e a ordem; a APN é a única com `fluxo` preenchido, o que a
  torna somente leitura para o `/admin` e manda o consultor para o wizard curto.
  Para liberar uma categoria travada, desmarque *em implementação*.
  O `CATS` do `index.html` é só o fallback offline — vale sincronizá-lo quando a
  mudança for estrutural, mas ele não é a fonte de verdade.
- **Valor por evento/negociação:** na etapa *Produto*, **"Editar valor"** libera a
  mensalidade (recorrentes) ou o valor à vista (avulsos); o total e o cronograma
  recalculam sozinhos. Pede autorização de um gerente ou da diretoria.
- **Pagamento:** na etapa *Pagamento*, **"Editar valores"** libera entrada, número de
  parcelas, valores, datas e formas, com o total como âncora. Também pede autorização.

## Deploy

Roda na **prod.solucoes (187.77.48.164)**, ao lado do kanban, do CRM, do formulário
financeiro e do Conecta ID. Mesmo desenho dos vizinhos:

```text
Cloudflare (proxied, Full strict)
   └── nginx do HOST  ── TLS com o Origin Certificate curinga *.sejaap.com.br
          └── 127.0.0.1:8095  →  container lobby-frontend (nginx)
                 ├── /            index.html · admin.html
                 └── /api/        →  lobby-backend (Django + gunicorn)
                                        └── db-tunnel → Postgres na db-sejaap:5437
```

- **Domínio:** <https://lobby.sejaap.com.br>. O DNS **precisa ficar proxiado** — em
  "DNS only" o navegador fala direto com a VPS e recusa o Origin Certificate.
- **Porta de loopback:** 8095. As vizinhas já estão tomadas: 8090 CRM, 8091 kanban,
  8092 kanban-mcp, 8093 formulários, 8094 identidade-api.
- **Rede:** o backend entra na `identidade-net`, criada pela stack do Conecta ID, para
  alcançar `identidade-api:8000`. Ela é `external: true` — se o Conecta ID não estiver
  de pé, o `up` falha alto, e isso é proposital.
- **Migrations não rodam sozinhas.** Aplique à mão antes de trocar o container, como
  nos outros apps da casa.

Passo a passo, portas, chaves de túnel e o procedimento de rotação da chave de
aplicação ficam em [`deploy/RUNBOOK.md`](deploy/RUNBOOK.md).

### Se o backend cair

O lobby **continua de pé**. O nginx resolve o backend a cada requisição (não na
inicialização), então ele sobe e serve o HTML mesmo sem a API; o `/api/catalogo`
devolve 502, o `boot()` captura e a página abre com o catálogo embutido. O consultor
segue cotando a preço de tabela — só não consegue negociar, porque não há como
autorizar.
