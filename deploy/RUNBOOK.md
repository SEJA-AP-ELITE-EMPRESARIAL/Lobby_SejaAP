# RUNBOOK — Lobby Seja AP

Operação do Lobby na **prod.solucoes (187.77.48.164)**.

> **Estado:** a stack está escrita e testada localmente, mas **ainda não foi
> provisionada na VPS**. Os passos de instalação abaixo são o roteiro do primeiro
> deploy, não a descrição de algo que já existe.

## Topologia

| | |
|---|---|
| App | `prod.solucoes.sejaap` — **187.77.48.164**, `/opt/conecta/app/Lobby_SejaAP` |
| Banco | `lobby` no Postgres da **db-sejaap** — 179.197.237.95:**5437**, por túnel SSH |
| Domínio | <https://lobby.sejaap.com.br> — Cloudflare **proxied**, SSL Full (strict) |
| Porta de loopback | **8095** |
| Identidade | Conecta ID, por `identidade-api:8000` na rede `identidade-net` |

Portas vizinhas já tomadas nesta VPS — confira antes de mexer:
8090 conecta-crm · 8091 kanban-frontend · 8092 kanban-mcp · 8093 formularios ·
8094 identidade-api (admin, só por túnel SSH).

Portas de banco na db-sejaap: 5433 kanban · 5434 CRM · 5435 formulários ·
5436 identidade · **5437 lobby**.

## Primeiro deploy

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

`lobby.sejaap.com.br` → **A** → 187.77.48.164, **Proxied**. Hoje ele aponta para o
Cloudflare Pages; virar o registro é o corte.

**Não apague o projeto no Pages junto.** Ele é o rollback enquanto a migração não
estabilizar — basta reapontar o DNS de volta.

## Deploy do dia a dia

```bash
cd /opt/conecta/app/Lobby_SejaAP
git pull --ff-only origin main
docker compose up -d --build
docker compose exec backend python manage.py migrate   # só se houver migration nova
```

Migrations **nunca** rodam no boot. É o padrão da casa, e existe para que uma
migration ruim não suba junto com o container num horário movimentado.

## Verificações

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://lobby.sejaap.com.br/            # 200
curl -s https://lobby.sejaap.com.br/api/catalogo | head -c 200                   # JSON público
curl -s -o /dev/null -w '%{http_code}\n' -X PUT https://lobby.sejaap.com.br/api/catalogo  # 401
```

O `GET /api/catalogo` **tem que continuar anônimo** — é a primeira chamada de toda
venda. Se ele passar a pedir credencial, o lobby quebra para todo consultor em campo.

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
- **A trava do valor por venda ainda é cosmética.** O valor negociado vai ao webhook do
  n8n sem validação do servidor; `edicao_autorizada` e `autorizado_por` no payload são
  **auditoria**, não controle de acesso. Fechar isso é a TSK-121.
- **`functions/` está superseded.** É a implementação anterior em Cloudflare Pages
  Functions, com senha compartilhada e catálogo em KV. Nunca foi ao ar.
