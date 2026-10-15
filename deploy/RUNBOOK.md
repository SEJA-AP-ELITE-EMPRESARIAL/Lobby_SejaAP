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
| Departamentos | `lobby-departamentos` — traz a lista do Omie para o dropdown do Elite e da APN, de hora em hora; sem porta |
| Categorias | `lobby-categorias` — traz as categorias financeiras para o campo Categoria da APN, de hora em hora, pelo `ListarCategorias` do Omie com a mesma chave dos departamentos; sem porta |
| Conecta (TSK-1059) | O Admin Lobby do Conecta chama `/api/integracao/*` e algumas rotas de administração com a asserção `X-Conecta-Assercao` (ADR-002 §13). Precisa de `CONECTA_ASSERCAO_CHAVES` e de `LOBBY_REDIS_URL`; sem eles, 503 `INTEGRACAO_DESLIGADA` e nada mais muda |

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

Preencha `DJANGO_SECRET_KEY`, `DATABASE_URL`, `IDENTIDADE_APP_KEY` e, para os
departamentos, `OMIE_APP_KEY` e `OMIE_APP_SECRET`.

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
publica no GitHub e mais nada. Enquanto ninguém rodar os comandos abaixo,
produção continua servindo o build anterior.

**Qual dos três roteiros usar** — a diferença é só se há migration nova:

| Mudou | Roteiro |
|---|---|
| Só `index.html` / `admin.html` | [Só o front](#só-o-front) |
| Backend, **sem** migration nova | [Front + backend](#front--backend) |
| Backend, **com** migration nova | [Com migration](#com-migration-a-ordem-importa) — a ordem é outra, e importa |

Os três começam no mesmo lugar:

```bash
ssh prod.solucoes.sejaap
cd /opt/conecta/app/Lobby_SejaAP
```

**O `sudo` não é opcional** em nenhum deles. O `.env` é um link para
`/opt/conecta/env/lobby.env`, que é `root:600` — sem sudo o compose morre com
`permission denied` antes de construir qualquer coisa. O usuário `deploy` está no
grupo sudo e passa sem senha.

### Só o front

```bash
git pull --ff-only origin main
sudo docker compose up -d --build --no-deps frontend
```

O `--no-deps` evita recriar backend e túnel por nada. E o `--build` é obrigatório:
os dois HTML são **copiados para dentro da imagem** (`deploy/frontend/Dockerfile`),
não montados por volume — editar o arquivo na VPS não muda o que o nginx serve.

### Front + backend

```bash
git pull --ff-only origin main
sudo docker compose up -d --build
```

### Com migration: a ordem importa

Migrations **nunca** rodam no boot. É o padrão da casa, e existe para que uma
migration ruim não suba junto com o container num horário movimentado. O que
segue é COMO rodá-las sem derrubar o lobby no meio.

```bash
git pull --ff-only origin main

# 1. Rede de segurança: nomeie as imagens que estão no ar ANTES de sobrescrevê-las
sudo docker tag lobby-sejaap-backend:latest  lobby-sejaap-backend:$(git rev-parse --short HEAD@{1})
sudo docker tag lobby-sejaap-frontend:latest lobby-sejaap-frontend:$(git rev-parse --short HEAD@{1})
# A imagem do sincronizador só existe depois do primeiro deploy com ele (TSK-503).
sudo docker tag lobby-sejaap-departamentos:latest lobby-sejaap-departamentos:$(git rev-parse --short HEAD@{1})
# A do sincronizador de categorias, só depois do primeiro deploy com ele (TSK-877).
sudo docker tag lobby-sejaap-categorias:latest lobby-sejaap-categorias:$(git rev-parse --short HEAD@{1})

# 2. Construir SEM trocar o que está servindo
sudo docker compose build

# 3. Migrar com a imagem NOVA, ainda sem trocar os containers
sudo docker compose run --rm --no-deps backend python manage.py migrate

# 4. Só agora trocar
sudo docker compose up -d
```

**Por que não é `up -d --build` e depois `migrate`.** Essa ordem — que este
runbook mandou fazer até 25/08/2026 — põe o **código novo servindo tráfego contra
o schema velho**. Basta o código novo ler uma coluna que a migration ainda não
criou para `GET /api/catalogo` responder 500 **para todo consultor em campo** até
o migrate terminar. Aconteceria na entrega do Formulário DH, que acrescentou
`Produto.fluxo` e o lê no serializer.

**E por que não dá para migrar antes do `build`.** O `compose exec backend` roda
o código da imagem que está no ar — a **velha**, que não contém as migrations
novas. Daí o `compose run` do passo 3: ele sobe um container efêmero **com a
imagem recém-construída**, aplica as migrations e sai, sem tocar no que está
servindo. O `--rm` remove o container ao fim; o `--no-deps` evita recriar o túnel,
que já está de pé (e é dele que o backend precisa para achar o banco).

**O passo 1 não é zelo.** O `build` sobrescreve o `:latest`; sem taguear antes, a
imagem anterior vira dangling e o rollback vira caça por ID. `HEAD@{1}` é o commit
em que a VPS estava **antes** do `pull` (conferido na VPS em 25/08/2026: depois
do pull para `8fea667`, ele resolvia para `795853f`) — se preferir não depender
do reflog, use o SHA que o `git log --oneline -1` mostrava antes.

**Rollback não desfaz migration.** Voltar a imagem devolve o código anterior, não
o schema. Migration com `desfazer` (as deste repo têm) se reverte à mão:
`sudo docker compose run --rm --no-deps backend python manage.py migrate <app> <número anterior>`.
Confira o `desfazer` antes: algumas se recusam a reverter de propósito — a
`catalogo/0007` não recria o produto que apagou, e a `0010` para se achar venda.
A `vendas/0003` (TSK-1056) alarga a coluna `fluxo` do comprovante para caber
`dh_avaliacao`; o desfazer a estreita de volta e o Postgres recusa enquanto
houver comprovante do DH - Avaliação — de propósito, não apague comprovante para
reverter.

**Migration que confere dado antes de mexer no schema** é comum aqui, e é
deliberado: ela para com a mensagem em português em vez de deixar o Postgres
reclamar de um índice sem dizer qual linha é o problema. Nada fica pela metade
(é uma transação). Duas que fazem isso:

- `catalogo/0003` procura sigla de produto repetida antes de criar o índice único;
- `catalogo/0010` procura venda emitida na categoria `palestras` antes de
  renomear o slug dela para `produtos`;
- `catalogo/0012` (TSK-1056) converte o Avaliações (`avaliacoes`) em DH - Avaliação
  e para se a sigla LBA já estiver em uso, se houver dois produtos `avaliacoes`,
  ou se ele não for o avulso esperado (com outro fluxo, ou recorrente). Banco sem
  o produto: não faz nada.

Nesses casos: corrija o dado (pelo `/django-admin/`) e rode o `migrate` de novo.

### DH - Avaliação (TSK-1056): o deploy bloqueia a venda dele até o webhook

O Avaliações vendia pelo fluxo de tabela, com o webhook do Elite. Depois deste
deploy ele é o DH - Avaliação, com webhook próprio — e a constante
`DH_AVALIACAO_SEND_WEBHOOK_URL` do `index.html` nasce **vazia**, porque o fluxo do
n8n ainda não existe. Enquanto ela estiver vazia, o consultor abre e preenche,
mas não consegue finalizar a venda. **Combine a data com o dono antes de
publicar.**

1. Roteiro [Com migration](#com-migration-a-ordem-importa): ele aplica
   `catalogo/0012`, `dh_avaliacao/0001` e `vendas/0003` antes de trocar os
   containers. Front e backend sobem juntos: um front antigo não sabe abrir um
   produto `dh_avaliacao`.
2. Confira a conversão:

   ```bash
   sudo docker compose exec backend python manage.py shell -c \
     "from apps.catalogo.models import Produto as P; p=P.objects.get(slug='avaliacoes'); print(p.nome, p.sigla, p.fluxo, p.valor)"
   # DH - Avaliação LBA dh_avaliacao None
   ```

3. O `CATS` embutido no `index.html` traz o DH - Avaliação com descrição, duração
   e ícone escritos na TSK-1056, sem acesso ao banco de produção. Gere o bloco de
   verdade com `manage.py exportar_cats_do_front` e, se diferir, cole-o no
   `index.html` num deploy só de front.
4. Quando o n8n tiver o fluxo: preencha `DH_AVALIACAO_SEND_WEBHOOK_URL` e publique
   [Só o front](#só-o-front). Os valores de Relatório e Devolutiva (R$ 150 e
   R$ 450, livres, até alguém salvar) ficam na aba **DH - Avaliação** do `/admin`.

### Integração com o Conecta (TSK-1059): deploy, e só depois ligar

São dois momentos, e o primeiro não liga nada:

**1. Deploy do código** — roteiro [Com migration](#com-migration-a-ordem-importa)
(construir, migrar, só então subir). Front e backend juntos: o `index.html` novo
monta o cronograma e o departamento pela categoria do produto, e o backend novo
é quem manda a regra de cada categoria. As migrations, em ordem:

| Migration | O que faz | Desfazer |
|---|---|---|
| `catalogo/0013` | Esquema: `Vigencia.encerrado_por`, `Vigencia.chave` de 40 para 60, campo `travada`; `PublicacaoCatalogo.alvos` | Sim. Recusa se já houver `Vigencia.chave` com mais de 40 caracteres (só existe se alguma categoria tiver slug com mais de 30) |
| `catalogo/0014` | Esquema: `PoliticaCobranca.categoria` e a constraint de três casos | Sim |
| `catalogo/0015` | Dado: copia a regra geral de cobrança para **cada** categoria, com a linha do tempo aberta na data da vigência da geral | Sim. Com todas as categorias iguais, a regra delas volta para a geral; com regras diferentes, a geral fica como está e as categorias voltam a segui-la (o que foi configurado por categoria se perde) |
| `catalogo/0016` | Dado: abre a linha do tempo da trava (`travada`) de cada categoria | Sim. Apaga a linha do tempo da trava |
| `departamentos/0003` | Esquema: `ConfiguracaoDepartamento.categoria` e `copiada_de` | Sim |
| `departamentos/0004` | Dado: a última escolha de departamento de hoje vira a de **cada** categoria, com o mesmo autor e a mesma data | Sim. Com todas as categorias iguais, a escolha delas vira uma gravação sem categoria; com escolhas diferentes, volta a valer a última de antes do deploy |

No dia do deploy **nenhuma venda muda**: cada categoria nasce com a regra geral e
com a escolha de departamento de hoje. Para conferir, antes e depois:

```bash
curl -s https://lobby.sejaap.com.br/api/departamentos | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['versao'], d['modo'])"
# a mesma versão antes e depois: nenhum lobby aberto acha que a lista mudou
curl -s https://lobby.sejaap.com.br/api/catalogo | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['cobranca']); [print(c['id'], c['cobranca']) for c in d['cats']]"
# toda categoria com a mesma regra do `cobranca` do envelope
```

O `/admin` de hoje segue funcionando com o corpo de sempre — e o backend aplica o
que ele salva a **todas** as categorias. Quando as categorias divergem (o Conecta
mudou uma), a aba avisa e salvar unifica.

**2. Ligar a integração** — quando o Conecta tiver a chave (o par nasce no
servidor do Conecta, nunca aqui) e a TSK-1060 estiver pronta para chamar:

1. **Redis.** A decisão de 15/10/2026 (ADR-002 §13.2) é usar o Redis do Conecta
   ID, índice 3: `LOBBY_REDIS_URL=redis://identidade-redis:6379/3`. **Hoje o
   `identidade-redis` só está na rede `identidade_interna`**, que a stack do
   Conecta ID fecha de propósito ("os apps NÃO entram"), e o backend do Lobby só
   está na `identidade-net`. Antes de preencher, alguém decide:
   - pôr o serviço `redis` do Conecta ID também na `identidade-net` (mudança no
     compose do conecta-id; abre aquele Redis, que não tem senha, a todo app da
     `identidade-net`); ou
   - dar ao Lobby um Redis próprio no compose dele (um serviço `redis:7-alpine`
     sem porta publicada, só na rede `default`), e apontar
     `LOBBY_REDIS_URL=redis://<serviço>:6379/0`.
   O código não muda em nenhum dos dois. Sem Redis alcançável, a integração fica
   em 503 e o resto do Lobby segue igual (os tetos de chamada falham abertos).

   **Antes de preencher, reveja os `LOBBY_RATE_*`.** Com o `LocMemCache` de hoje
   cada um dos 3 workers conta sozinho e o teto real é ~3× o configurado; com
   Redis ele passa a valer o número exato e sobrevive a deploy. Num evento com
   vários consultores atrás do mesmo IP, `LOBBY_RATE_CATALOGO=240/hour` vira 240
   de verdade (hoje são ~720) — e o `index.html` cai no catálogo EMBUTIDO quando
   recebe 429, ou seja, vende com preço possivelmente velho sem erro na tela.
   Para manter o comportamento de hoje, triplique os valores no mesmo deploy
   (por exemplo `LOBBY_RATE_CATALOGO=720/hour`, `LOBBY_RATE_COMPROVANTE=360/hour`,
   `LOBBY_RATE_LOGIN=360/hour`, e os de 600/hour para 1800/hour).
2. **Chave pública.** `CONECTA_ASSERCAO_CHAVES` no `.env` (formato no
   `.env.example`: conjunto JWK, um `kid` por chave). Nunca a privada: o Lobby
   recusa um JWK com `d`.
3. `sudo docker compose up -d backend` e, no log da subida:

   ```bash
   sudo docker compose logs backend | grep "integração com o Conecta"
   # integração com o Conecta ligada; kids aceitos: conecta-AAAAMM-a
   # ou: integração com o Conecta DESLIGADA: <motivo>
   ```

4. Do lado do Conecta, `GET /api/integracao/saude` com uma asserção válida
   responde `{"kids": [...], "emissor": "conecta", "audiencia": "lobby"}`.

**Troca de chave** é a do ADR-002 §8.2: as duas públicas juntas em
`CONECTA_ASSERCAO_CHAVES`, reiniciar, conferir o `saude`, virar o assinador no
Conecta, esperar 5 minutos, tirar a antiga. **Vazamento** (§8.3): tirar o `kid`
comprometido daqui e reiniciar vem PRIMEIRO.

**Log.** Uma linha por chamada no logger `lobby.integracao`, com `kid`, `jti`,
`sub`, url, método, status, `codigo`, tempo e IP — nunca a asserção. Recusa de
relógio traz a diferença medida (`detalhe=venceu há 75s`): arrume o NTP, não a
tolerância.

## Verificações

```bash
sudo docker compose ps                                                           # 3 healthy + departamentos e categorias running
sudo docker compose logs --tail 3 departamentos                                  # "Departamentos sincronizados: N ativos…"
curl -s https://lobby.sejaap.com.br/api/departamentos | head -c 200              # JSON público, só os ativos
sudo docker compose logs --tail 3 categorias                                     # "Categorias financeiras sincronizadas: N ativas…"
curl -s https://lobby.sejaap.com.br/api/categorias-financeiras | head -c 200     # JSON público, só as turmas da APN (grupo 1.10)
curl -s -o /dev/null -w '%{http_code}\n' https://lobby.sejaap.com.br/            # 200
curl -s https://lobby.sejaap.com.br/api/catalogo | head -c 200                   # JSON público
curl -s -o /dev/null -w '%{http_code}\n' -X PUT https://lobby.sejaap.com.br/api/catalogo  # 401
```

Com migration nova, confirme também que ela subiu. Aqui o `exec` é o certo — o
container que está no ar já é o da imagem nova:

```bash
sudo docker compose exec backend python manage.py showmigrations catalogo vendas | tail -5
curl -s https://lobby.sejaap.com.br/api/catalogo | head -c 300    # o campo novo aparece?
```

A segunda linha vale mais que a primeira: `showmigrations` diz o que a tabela
`django_migrations` registra, e o que interessa é o que a aplicação **responde**.

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

Seis alertas: fora do ar, catálogo vazio, o n8n parou de validar, ninguém na
diretoria, segredo do n8n ausente e departamentos sem sincronizar com o Omie há
mais de 3 horas. Os dois que ninguém adivinharia sozinho são
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

**Omie fora do ar, ou chave do Omie revogada** → o lobby segue vendendo, e o
dropdown de departamento fica com a última lista sincronizada — nada some. O
motivo está no log do sincronizador (é a `faultstring` do Omie):
`sudo docker compose logs --tail 20 departamentos`. Chave nova vai em
`/opt/conecta/env/lobby.env` (`OMIE_APP_KEY` / `OMIE_APP_SECRET`) e entra com
`sudo docker compose up -d --force-recreate --no-deps departamentos` — ele
sincroniza na subida. Para trazer uma mudança do Omie na hora, sem esperar a
rodada: `sudo docker compose exec backend python manage.py sincronizar_departamentos`.

O mesmo vale para o **campo Categoria da APN**, que usa a mesma chave: ele fica
com a última lista sincronizada. O motivo está em
`sudo docker compose logs --tail 20 categorias`, e a rodada na hora é
`sudo docker compose exec backend python manage.py sincronizar_categorias_financeiras`.
Se nenhuma rodada deu certo ainda, o campo nem aparece na APN. "Nenhuma das N
categorias do Omie está sob 1.10" quer dizer que o grupo APN foi renumerado no
Omie: ajuste `CATEGORIAS_FINANCEIRAS_PREFIXOS` no `lobby.env` e recrie o
container `categorias`.

**`AUTH_CENTRAL_ATIVO=false` não é rollback, é tranca.** Não há senha local neste app;
desligar tira todo mundo, inclusive a diretoria.

## Rotação da chave de aplicação

Máximo **2 chaves ativas** por app no Conecta ID — é o que permite rotacionar sem
janela de indisponibilidade:

1. `cadastrar_aplicacao lobby --emitir-chave` (a antiga continua valendo)
2. Atualize `IDENTIDADE_APP_KEY` no `.env` e reinicie o backend
3. Revogue a antiga no admin do Conecta ID

## Pendências conhecidas

- **Sem cache compartilhado (até ligar a integração do Conecta).** O throttle das
  rotas anônimas usa `LocMemCache`, que é por processo do gunicorn (3 workers) e zera
  a cada deploy. Os tetos são contenção de rajada, não limite exato. Preencher
  `LOBBY_REDIS_URL` resolve, e desde a TSK-1059 o pacote `redis` está no
  `requirements.txt` — a armadilha de "sobe e morre no primeiro throttle" acabou. Com
  o Redis fora do ar, os tetos falham abertos (`config/cache.py`) em vez de virar 500
  no lobby. Falta a rede: ver [Integração com o Conecta](#integração-com-o-conecta-tsk-1059-deploy-e-só-depois-ligar).
- **A origem aceita conexão de fora da Cloudflare** (medido em 21/09/2026, TSK-195).
  O IP do visitante vem do `CF-Connecting-IP` (`backend/config/real_ip.py`), e ele só é
  confiável quando a requisição passou pela Cloudflare. Mas a 443 da .164 responde a
  qualquer um (`ufw`: `443 ALLOW Anywhere`; nenhuma lista de ranges no nginx), então
  quem bater direto no IP da VPS com `Host: lobby.sejaap.com.br` escolhe o próprio IP
  — para o throttle e para o que vai ao Conecta ID. Com isso ele foge do próprio balde
  e também consegue encher o balde de outro IP público (o de um escritório, por
  exemplo). Endereço de dentro (loopback, rede privada) é recusado no header, então o
  balde do healthcheck fica fora do alcance. O fecho é de infra, igual ao da
  TSK-286 no ConectaAP: aceitar na 443 só os ranges da Cloudflare (`geo` sobre
  `$realip_remote_addr`, nunca `allow`/`deny` junto de `set_real_ip_from`).
- **Não tire a nuvem laranja do `lobby.sejaap.com.br` sem antes configurar `real_ip`
  no nginx do host** (TSK-195). Sem a Cloudflare na frente, o `CF-Connecting-IP` some
  e o `real_ip.py` não tem outro header confiável para usar: o `X-Real-IP` é o gateway
  do docker. Todo visitante passa a ter o IP do container do nginx, e isso vale para
  o throttle (um balde só para todo mundo) e para o login: o Conecta ID passa a ver um
  IP só, e o bloqueio por origem de lá vira bloqueio geral, com um consultor errando a
  senha trancando os outros.
- **A trava do valor por venda ainda não tem efeito — mas o motivo mudou.** O lado do
  servidor está pronto e no ar: `/api/venda/comprovante` assina os valores e
  `/api/venda/validar` recusa comprovante forjado, reusado ou adulterado. Só que
  **o fluxo do n8n ainda não chama `/api/venda/validar`**, então a assinatura é
  emitida e ignorada. O `LOBBY_N8N_TOKEN` já existe no `.env` do servidor e falta
  ser inserido no fluxo pelo responsável pelo n8n — passo a passo em
  `N8N-VALIDACAO.md`. Enquanto isso, o alerta *"O n8n parou de validar as vendas"*
  fica calado de propósito: ele só toca depois da primeira validação bem-sucedida,
  para não nascer disparado.
- **`functions/` foi removida** (era a implementação anterior em Cloudflare Pages
  Functions, com senha compartilhada e catálogo em KV; nunca foi ao ar). Vários
  comentários do `backend/apps/catalogo/` ainda a citam, de propósito, porque
  explicam de onde cada regra veio — leia com `git show 338e932 -- functions/`.
