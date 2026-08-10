# RUNBOOK — Lobby Seja AP

Operação do Lobby na **prod.solucoes (187.77.48.164)**.

> **Estado: no ar.** A stack foi provisionada entre 06 e 07/08/2026 e
> `lobby.sejaap.com.br` sai da VPS, não mais do Cloudflare Pages. Para publicar
> uma mudança, vá direto para [Deploy do dia a dia](#deploy-do-dia-a-dia) — a
> seção de primeiro deploy é o roteiro de reprovisionamento, não o que fazer hoje.

## Topologia

| | |
|---|---|
| App | `prod.solucoes.sejaap` — **187.77.48.164**, `/opt/conecta/app/Lobby_SejaAP` |
| Banco | `lobby` no Postgres da **db-sejaap** — 179.197.237.95:**5437**, por túnel SSH |
| Domínio | <https://lobby.sejaap.com.br> — Cloudflare **proxied**, SSL Full (strict) |
| Porta de loopback | **8095** (front, é a que o vhost do host encaminha) |
| Coleta de métricas | **8096** (backend direto, só o Prometheus — sem caminho público) |
| Identidade | Conecta ID, por `identidade-api:8000` na rede `identidade-net` |

Portas vizinhas já tomadas nesta VPS — confira antes de mexer:
8090 conecta-crm · 8091 kanban-frontend · 8092 kanban-mcp · 8093 formularios ·
8094 identidade-api (admin, só por túnel SSH).

As duas portas do Lobby não são intercambiáveis. A **8095** é o container do
front, e o vhost do host manda `location /` inteiro para ela — tudo que responde
ali é público. A **8096** é o backend direto e existe só para o Prometheus
alcançar o `/metrics`; expor o `/metrics` pela 8095 o publicaria em
`lobby.sejaap.com.br/metrics`.

Portas de banco na db-sejaap: 5433 kanban · 5434 CRM · 5435 formulários ·
5436 identidade · **5437 lobby**.

## Primeiro deploy (já executado — roteiro de reprovisionamento)

Estes oito passos já rodaram. Ficam registrados para reconstruir a stack do zero
(VPS nova, disaster recovery) e para explicar de onde veio cada arquivo em
`/opt/conecta/env/`. Não repita nada daqui num deploy comum.

### 1. Banco na db-sejaap

```sql
CREATE USER lobby_app WITH PASSWORD '<senha forte>';
CREATE DATABASE lobby OWNER lobby_app;
```

O container `lobby-postgres` precisa escutar em `127.0.0.1:5437`, como os irmãos.

### 2. Chave do túnel

Par de chaves dedicado, com a autorização restrita no destino — `restrict` sozinho
**não** impede execução de comando, quem faz isso é o `command="/bin/false"`:

```
restrict,port-forwarding,permitopen="127.0.0.1:5437",command="/bin/false" ssh-ed25519 AAAA...
```

Na VPS do app, em `/opt/conecta/env/`, modo 600:

- `lobby_db_tunnel_key` — a chave privada
- `lobby_db_known_hosts` — a host key da db-sejaap (fixada, sem `ssh-keyscan` no deploy)

### 3. Aplicação no Conecta ID

No container do Conecta ID:

```bash
docker exec identidade-api python manage.py cadastrar_aplicacao lobby \
    --nome "Lobby Seja AP" --emitir-chave
```

**Sem `--gestao`.** Dar gestão ao Lobby significaria dar a ele leitura da base
inteira de identidades da empresa. O segredo aparece **uma única vez** — guarde.

Depois, conceda acesso ao app `lobby` a cada pessoa que vai negociar ou publicar
(admin do Conecta ID ou `POST /api/v1/acessos` pelo kanban). Quem não tem acesso
recebe uma mensagem própria no login, não "senha incorreta".

### 4. `.env`

```bash
cp .env.example /opt/conecta/env/lobby.env   # chmod 600
ln -s /opt/conecta/env/lobby.env /opt/conecta/app/Lobby_SejaAP/.env
```

Preencha `DJANGO_SECRET_KEY`, `DATABASE_URL` e `IDENTIDADE_APP_KEY`.

### 5. Subir

```bash
cd /opt/conecta/app/Lobby_SejaAP
docker compose up -d --build
docker compose exec backend python manage.py migrate     # à mão, sempre
```

A `0002_semear_catalogo` planta a tabela de preços que está em produção hoje. Ela é
idempotente (`get_or_create` por slug), então rodar de novo não duplica nada.

### 6. Primeira pessoa da diretoria

Não existe login local — nem para superusuário. Sem este passo, ninguém alcança o
`/django-admin/`:

```bash
docker exec lobby-backend python manage.py promover_no_lobby \
    fulano@sejaap.com.br --papel diretoria
```

### 7. nginx do host

```bash
cp deploy/nginx-host-lobby.conf /etc/nginx/sites-available/lobby.conf
ln -s /etc/nginx/sites-available/lobby.conf /etc/nginx/sites-enabled/lobby.conf
nginx -t && systemctl reload nginx
```

O nginx desta VPS é anterior ao 1.25.1 — a sintaxe é `listen 443 ssl http2;`.

### 8. DNS

`lobby.sejaap.com.br` → **A** → 187.77.48.164, **Proxied**. Registro já virado —
o domínio serve a VPS.

**Não apague o projeto no Pages junto.** Ele é o rollback enquanto a migração não
estabilizar — basta reapontar o DNS de volta.

## Deploy do dia a dia

**Não existe deploy automático.** Não há GitHub Actions neste repo: push em `main`
publica no GitHub e mais nada. Enquanto ninguém rodar o comando abaixo, produção
continua servindo o build anterior.

```bash
ssh prod.solucoes.sejaap
cd /opt/conecta/app/Lobby_SejaAP
git pull --ff-only origin main
sudo docker compose up -d --build
sudo docker compose exec backend python manage.py migrate   # só se houver migration nova
```

**O `sudo` não é opcional.** O `.env` é um link para `/opt/conecta/env/lobby.env`,
que é `root:600` — sem sudo o compose morre com `permission denied` antes de
construir qualquer coisa. O usuário `deploy` está no grupo sudo e passa sem senha.

Mudança só de `index.html` ou `admin.html` não precisa mexer no backend:

```bash
sudo docker compose up -d --build --no-deps frontend
```

O `--no-deps` evita recriar backend e túnel por nada. E o `--build` é obrigatório:
os dois HTML são **copiados para dentro da imagem** (`deploy/frontend/Dockerfile`),
não montados por volume — editar o arquivo na VPS não muda o que o nginx serve.

Migrations **nunca** rodam no boot. É o padrão da casa, e existe para que uma
migration ruim não suba junto com o container num horário movimentado.

## Verificações

```bash
sudo docker compose ps                                                           # 3 healthy
curl -s -o /dev/null -w '%{http_code}\n' https://lobby.sejaap.com.br/            # 200
curl -s https://lobby.sejaap.com.br/api/catalogo | head -c 200                   # JSON público
curl -s -o /dev/null -w '%{http_code}\n' -X PUT https://lobby.sejaap.com.br/api/catalogo  # 401
```

**Confirme que o build novo está no ar**, e não o anterior. Container saudável
não quer dizer HTML novo — a imagem pode ter sido construída antes do `git pull`:

```bash
curl -s https://lobby.sejaap.com.br/ | grep -c '<trecho que você acabou de mudar>'
```

Se der `0`, foi cache do navegador que enganou você ou o `--build` não pegou. O
`/` responde `Cache-Control: no-cache` e a Cloudflare devolve `cf-cache-status:
DYNAMIC`, então HTML velho no `curl` é problema de servidor, não de borda.

O `GET /api/catalogo` **tem que continuar anônimo** — é a primeira chamada de toda
venda. Se ele passar a pedir credencial, o lobby quebra para todo consultor em campo.

## Monitoramento

O Lobby expõe `/metrics` para o Prometheus, fechado por token
(`LOBBY_METRICS_TOKEN`). **Sem o token configurado o endpoint responde 404** — é
o padrão, e é deliberado: aberto, ele entrega o volume de vendas e quantas
pessoas podem autorizar desconto.

A coleta é pela **8096** (backend direto), nunca pela 8095 — ver *Topologia*.

```bash
# na prod.solucoes, com o token que está em /opt/conecta/env/lobby.env
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8096/metrics | head -5
```

O arquivo de token do Prometheus (`/opt/monitoring/prometheus/lobby_token`) tem
que ser **644**. O container roda como `nobody`: com 600 do root ele não abre o
arquivo, o alvo fica DOWN com *permission denied* e o alerta de "fora do ar"
toca com o Lobby de pé.

Instalação, na ordem — os alertas antes do job fazem o Telegram tocar à toa,
porque o "fora do ar" trata NoData como alerta:

1. `deploy/monitoramento/prometheus-job-lobby.yml` → `prometheus.yml`, e reiniciar
2. Conferir em *Status > Targets* que `lobby` está **UP**
3. `deploy/monitoramento/alertas-lobby.yml` →
   `/opt/monitoring/grafana/provisioning/alerting/lobby.yml`, e reiniciar o Grafana

Cinco alertas: fora do ar, catálogo vazio, o n8n parou de validar, ninguém na
diretoria, e segredo do n8n ausente. Os dois que ninguém adivinharia sozinho são
o **catálogo vazio** (o front não quebra — cai na tabela embutida e vende com
preço velho) e a **validação parada** (o comprovante continua sendo assinado e
ninguém confere).

## Incidentes

**Conecta ID fora do ar** → ninguém autoriza negociação e ninguém entra no `/admin`.
O lobby continua vendendo a preço de tabela. O login responde **503**, nunca "senha
incorreta" — se todo mundo lesse "senha incorreta" ao mesmo tempo, a leitura natural
seria vazamento de credenciais.

**Backend fora do ar** → o lobby continua de pé, servindo o HTML e caindo no catálogo
embutido. `/api/catalogo` devolve 502 e o `boot()` captura. O nginx resolve o backend
a cada requisição, de propósito: com o nome resolvido só na inicialização, ele
recusaria subir sem o backend e derrubaria o lobby junto.

**`AUTH_CENTRAL_ATIVO=false` não é rollback, é tranca.** Não há senha local neste app;
desligar tira todo mundo, inclusive a diretoria.

## Rotação da chave de aplicação

Máximo **2 chaves ativas** por app no Conecta ID — é o que permite rotacionar sem
janela de indisponibilidade:

1. `cadastrar_aplicacao lobby --emitir-chave` (a antiga continua valendo)
2. Atualize `IDENTIDADE_APP_KEY` no `.env` e reinicie o backend
3. Revogue a antiga no admin do Conecta ID

## Pendências conhecidas

- **Sem cache compartilhado.** O throttle das rotas anônimas usa `LocMemCache`, que é
  por processo do gunicorn (3 workers) e zera a cada deploy. Os tetos são contenção de
  rajada, não limite exato. Basta preencher `LOBBY_REDIS_URL` para resolver.
- **A trava do valor por venda ainda não tem efeito — mas o motivo mudou.** O lado do
  servidor está pronto e no ar: `/api/venda/comprovante` assina os valores e
  `/api/venda/validar` recusa comprovante forjado, reusado ou adulterado. Só que
  **o fluxo do n8n ainda não chama `/api/venda/validar`**, então a assinatura é
  emitida e ignorada. O `LOBBY_N8N_TOKEN` já existe no `.env` do servidor e falta
  ser inserido no fluxo pelo responsável pelo n8n — passo a passo em
  `N8N-VALIDACAO.md`. Enquanto isso, o alerta *"O n8n parou de validar as vendas"*
  fica calado de propósito: ele só toca depois da primeira validação bem-sucedida,
  para não nascer disparado.
- **`functions/` está superseded.** É a implementação anterior em Cloudflare Pages
  Functions, com senha compartilhada e catálogo em KV. Nunca foi ao ar.
