"""
Cliente do login central (o Conecta ID) — o módulo que cada app instala.

FONTE DE VERDADE, desde a 1.5: no repositório do Conecta, que assumiu o papel
do Conecta ID, em `backend/backend/app/saas/conectaap/apps/identidade/cliente/
identidade_client.py`. Até a 1.4 era `conecta-id/cliente/identidade_client.py`.
Nos apps ele é copiado, não editado. Corrigiu algo? Corrija lá e recopie — um
cliente que diverge entre apps é a forma mais silenciosa de dois sistemas
passarem a tratar o mesmo erro de jeitos diferentes.

Só depende de Django e da biblioteca padrão, e não importa nada do Conecta: ele
roda dentro dos apps. Nada de `requests`: são poucas chamadas HTTP simples, e
uma dependência a mais em cinco apps custa mais do que as vinte linhas de
`urllib` que ela pouparia.

## Como um app usa

    # settings.py
    AUTHENTICATION_BACKENDS = [
        "identidade_client.BackendIdentidade",
        "django.contrib.auth.backends.ModelBackend",   # fallback local (conta sem vínculo)
    ]
    IDENTIDADE_URL = "https://conecta.sejaap.com.br/api/identidade"
    IDENTIDADE_APP_KEY = "AppKey financeiro:..."       # do .env
    AUTH_CENTRAL_ATIVO = True                          # a chave que reverte tudo
    IDENTIDADE_RESOLVER_USUARIO = "apps.formularios.identidade.resolver_usuario"
    IDENTIDADE_NUM_PROXIES = 3                         # ver a 1.4

O `resolver_usuario` é do app, e é onde mora tudo que o Conecta ID não sabe:
criar o Perfil no financeiro, o nível de acesso e a equipe no kanban. O cliente
entrega quem é a pessoa; o app decide o que ela é lá dentro.

## 1.1 — a configuração de provisionamento

Desde a 1.1 a resposta do login traz `config_do_app`: o que quem administra
preencheu, no Conecta ID, sobre esta pessoa NESTE app. É a metade que faltava —
sem ela, conceder acesso no Conecta ID criava, no primeiro login, uma conta no
default mais baixo (sem equipe, sem papel), e quem administra ia criar a pessoa
de novo dentro do app. Daí saíam as contas duplicadas.

Leia sempre pela classe `Provisionamento`, nunca do dicionário cru: a carga é
digitada por gente e pode trazer um valor que este app não conhece. O contrato
é **provisionar melhor quando dá, nunca impedir o login**.

## 1.3 — por que o link de senha não serve

`TokenInvalido.motivo` diz por que um link REAL foi recusado: `"substituido"`
(emitiram outro depois dele — só o link mais recente vale), `"expirado"` ou
`"usado"`. `None` quando não há o que dizer: token inventado, ou conta
desativada. É o que permite a tela trocar o "link inválido ou expirado" por uma
frase que diz o que fazer; com a genérica, a pessoa pedia mais um link e anulava
o que estava a caminho (incidente de 28/09/2026). O código do erro continua
`token_invalido`: quem ainda usa a 1.2 recebe o mesmo erro de sempre.

## 1.4 — o IP do usuário conta da direita

O IP que vai ao Conecta ID (para o bloqueio por origem) saía do PRIMEIRO item do
`X-Forwarded-For`, que é o que o próprio cliente escreve: cada proxy ACRESCENTA
ao cabeçalho, sem apagar o que veio antes. Com `X-Forwarded-For: <IP do
escritório>`, umas tentativas erradas trancavam o escritório inteiro, e trocar o
cabeçalho a cada tentativa anulava a camada por IP contra senha comum (kanban,
TSK-837, 30/09/2026).

Desde a 1.4 o IP é o N-ésimo a partir do fim, com N = os proxies em que o app
confia (`ip_do_request`). **Cada app precisa dizer quantos são:**

    IDENTIDADE_NUM_PROXIES = 3   # ex.: Cloudflare -> nginx do host -> nginx do front

Sem essa chave, vale o `REST_FRAMEWORK["NUM_PROXIES"]` do app, se ele tiver
um maior que zero. Sem nenhuma das duas, o cliente mantém o comportamento
antigo (o primeiro item), que é forjável, e avisa no log uma vez. `0` explícito
quer dizer "não há proxy": vale o `REMOTE_ADDR`.

## 1.5 — o Conecta no lugar do Conecta ID (TSK-1221)

Três decisões do dono, de 23/10/2026.

**Só o Conecta cria login e concede acesso**, pelo /admin, e nenhum app recebe
link ou token de senha. Saíram `criar`, `alterar`, `conceder_acesso`,
`configurar_acesso`, `revogar_acesso` e `emitir_token_de_senha` (a API já
recusava essas escritas com 403 `sem_permissao`). Ficam as leituras
(`verificar`, `buscar_por_email`, `listar_com_acesso`, `obter`, `saudavel`), a
semente do primeiro login (`resolver_com_vinculo`) e as chamadas de senha da
própria pessoa (`esqueci_senha`, `definir_senha`, `trocar_senha`), com rota no
Conecta desde a TSK-1217 (`api/v1/senha/esqueci`, `api/v1/senha/definir` e
`api/v1/identidades/<id>/senha`).

**Em manutenção, a tela diz que está em manutenção**, e não que deu erro. O 503
`erro: manutencao` vira `IdentidadeEmManutencao`, com `mensagem` (o texto do
aviso) e `previsao` (`datetime` com fuso, ou `None`). Ela é uma
`IdentidadeIndisponivel`, e o código que só conhece a 1.4 segue tratando como
indisponível; por isso o `except` dela vem ANTES:

    try:
        usuario = authenticate(request, username=email, password=senha)
    except identidade_client.IdentidadeEmManutencao as erro:
        # A tela: "Temporariamente indisponível / Em manutenção", o texto do
        # aviso e, se houver, a hora prevista para a volta.
        return Response({"erro": "manutencao", "mensagem": erro.mensagem,
                         "previsao": erro.previsao and erro.previsao.isoformat()},
                        status=503)
    except identidade_client.IdentidadeIndisponivel:
        ...   # "login indisponível, tente em instantes", como antes

A mesma captura vale nas outras telas que chamam o Conecta (esqueci e definir
senha).

**Desativar a pessoa no Conecta corta a sessão já aberta.** `revalidar_sessao`
pergunta ao Conecta, no máximo a cada 5 minutos por usuário, se a identidade
ainda vale neste app; só uma resposta clara de que não vale derruba a sessão.
Cortam a sessão a identidade desativada no Conecta e, desde a TSK-1253, também
o `Usuario` do Conecta desativado ou bloqueado (a saída comum de quem é da
equipe) e o acesso revogado neste app, com ou sem `pode_gerir_identidades`
(ver a 1.5.1). E o `identidade_id` do vínculo local tem de ser o
que o Conecta conhece: a importação do Conecta ID tem de preservar os UUIDs, ou
ninguém entra (`resolver_com_vinculo` recusa a conta ligada a outra identidade,
e o log diz qual vínculo corrigir), e a sessão já aberta cai a cada 5 minutos.
O app liga a revalidação onde confere a sessão:

- sessão do Django (kanban): um middleware logo depois do
  `AuthenticationMiddleware`:

      def revalidar_sessao_middleware(get_response):
          def middleware(request):
              if (request.user.is_authenticated
                      and not identidade_client.revalidar_sessao(request.user)):
                  logout(request)
              return get_response(request)
          return middleware

- JWT (Formulário, Lobby, Selo, CRM): na classe de autenticação, que roda a
  cada requisição (só na renovação não bastaria: o access dura horas):

      class JWTRevalidado(JWTAuthentication):
          def get_user(self, validated_token):
              usuario = super().get_user(validated_token)
              if not identidade_client.revalidar_sessao(usuario):
                  raise AuthenticationFailed("Sessão encerrada.", code="user_inactive")
              return usuario

  e na renovação, ANTES do `super().validate()` (que rotaciona o token). O
  `TokenRefreshSerializer` do simplejwt 5.3 não olha o usuário: sem isto, o
  front renova sem parar um token que a requisição seguinte recusa.

      # api_settings e InvalidToken são os do simplejwt (`rest_framework_simplejwt.settings`
      # e `.exceptions`): o `api_settings` do DRF não tem `USER_ID_FIELD`.
      class RenovacaoRevalidada(TokenRefreshSerializer):   # SIMPLE_JWT["TOKEN_REFRESH_SERIALIZER"]
          def validate(self, attrs):
              refresh = self.token_class(attrs["refresh"])
              usuario = get_user_model().objects.filter(**{
                  api_settings.USER_ID_FIELD: refresh.get(api_settings.USER_ID_CLAIM)}).first()
              if (usuario is None or not usuario.is_active
                      or not identidade_client.revalidar_sessao(usuario)):
                  raise InvalidToken("Sessão encerrada.")
              return super().validate(attrs)

  O Lobby não tem rota de renovação: lá basta a classe de autenticação.

  O token do DRF (o do MCP no kanban) se liga do mesmo jeito, na classe de
  autenticação dele: o middleware só vê a sessão.

E o `BackendIdentidade` recusa a conta local inativa, no `authenticate` e no
`get_user`, como o `ModelBackend`; e a conta ligada a uma identidade que o login
central recusa não cai mais no `ModelBackend` de fallback (a senha local antiga
dela não entra): o fallback fica para a conta sem vínculo, como o superusuário de
emergência. E o e-mail não casa mais com conta já ligada a outra identidade
(`resolver_com_vinculo`): até a 1.4, um e-mail reaproveitado no login central
herdava a conta e as permissões da pessoa anterior.

Também na 1.5: resposta que não é JSON (a página HTML de um proxy, um
`IDENTIDADE_URL` errado), JSON no formato errado e conexão cortada no meio da
resposta viram `IdentidadeIndisponivel`. Até a 1.4 subiam como uma exceção
qualquer, e a tela de login dava 500.

## 1.5.1 — o texto diz o que a API faz desde a TSK-1253

Nenhuma linha de lógica mudou. Desde a TSK-1253 o `ativo` do `obter` é o
estado real da pessoa (falso também com o `Usuario` do Conecta inativo ou
bloqueado), e cada app só enxerga quem tem acesso ATIVO a ele (o revogado dá
404 `nao_encontrado`). Com isso a `revalidar_sessao` da 1.5.0 já derrubava a
sessão nesses casos; a 1.5.1 só acerta o cabeçalho, o docstring dela e o log.

## 1.5.2 — o cliente se apresenta (TSK-1267)

Toda requisição leva `User-Agent: ConectaIdentidadeClient/<VERSAO>`. Sem ele, o
`urllib` se apresenta como `Python-urllib/3.x`, e a Cloudflare na frente do
Conecta barra esse nome: em 25/10/2026, testando de dentro dos apps em
produção, toda chamada levou 403 (`error code: 1010`), que o cliente traduz em
`IdentidadeIndisponivel` (e o `saudavel` dava False). Nenhuma outra linha de
lógica mudou.

## 1.5.3 — a chave fica no Conecta, e a resposta velha não vence a nova (TSK-1270)

Quatro pontos que as revisões das versões anteriores deixaram para esta.

**A chave do app não segue redirecionamento para outro destino.** Até a 1.5.2 o
`Authorization` ia por `add_header`, e o `urllib` repete esses cabeçalhos no
pedido que segue um 3xx, sem olhar para onde: um proxy mal configurado, ou um
`IDENTIDADE_URL` que cai num domínio que redireciona, entregava a chave a outro
host. Agora o cliente segue o 3xx pelo `_RedirecionamentoSemAChave`: para o
mesmo destino (mesmo esquema, host e porta), o pedido leva a chave, como antes;
para outro host, outra porta, ou de https para http, vai sem ela, e o que vier
de lá não vale como resposta do Conecta: o cliente levanta
`IdentidadeIndisponivel`, e o log diz para onde foi. Nem um 200 de outro host
vira login, e um 3xx de lá não é seguido. O `User-Agent` da 1.5.2 continua em
todo pedido, inclusive no redirecionado. Um `Location` que nem é URL também
vira `IdentidadeIndisponivel` (até a 1.5.2, um `ValueError` e 500 no login).
Um handler, e não o `add_unredirected_header`, porque este tiraria a chave
também do mesmo host, e reenviá-la à mão custaria uma ida a mais ao Conecta e a
regra de método do `urllib` (o POST que vira GET) reescrita aqui. De http para
https no mesmo host a chave também não vai (é outro esquema): o `IDENTIDADE_URL`
já tem de ser o `https://`.

**Uma resposta velha não apaga uma nova na revalidação.** Uma pergunta ao
Conecta que começou antes de a pessoa ser reativada, e terminou depois do novo
login dela, gravava a recusa por cima do login aceito, e a pessoa ficava
recusada por mais 5 minutos. Agora, ao lado da resposta, ficam a hora do último
login aceito (a hora em que ele é lembrado, depois de o Conecta aceitá-lo: ver
`lembrar_sessao_valida`) e a hora em que começou a pergunta que deu a última
recusa; e uma recusa mais velha que o último login não vale. A pergunta que
começou antes do login nem grava a recusa e, se ela gravar mesmo assim (outro
processo no mesmo Redis, uma pausa no meio da gravação), a leitura a descarta:
a ordem não depende de trava, que a API de cache do Django não tem como fazer
atômica. Vale com o LocMem (o padrão de quem não configura `CACHES`, como o
Formulário) e com o Redis. A resposta guardada continua o texto da 1.5.2, com as
horas em chaves ao lado: voltar a imagem de um app para a 1.5.2 não solta quem
estava recusado. Sem uma das horas (a 1.5.2 gravou a recusa, ou o cache cortou a
chave), a recusa vale, como até a 1.5.2: com o LocMem, cada pessoa ocupa até
quatro entradas, e o padrão do Django guarda 300 por processo, junto com o que
mais o app puser no cache; o app com muita gente sobe o `OPTIONS["MAX_ENTRIES"]`.
A regra que não muda: falha ou manutenção do Conecta nunca derrubam a sessão.

**`lembrar_sessao_valida(usuario)` é pública.** É o que o app chama quando o
próprio login acaba de ser aceito pela API sem passar pelo `BackendIdentidade`
(o Lobby e o Selo chamam o `verificar` direto), para valer como revalidação; e
ela entra na mesma regra de ordem. A privada `_lembrar_sessao_valida`, que o
Lobby usa por `getattr`, fica como apelido nesta versão e sai na próxima.

**O texto:** as rotas de senha existem no Conecta desde a TSK-1217, e o
cabeçalho da 1.5 e os docstrings de `esqueci_senha`, `definir_senha` e
`trocar_senha` diziam que não.

## Quem vem da 1.5.2 (Formulário e Lobby)

Nenhuma configuração muda, e voltar para a 1.5.2 (o rollback da imagem) também
não pede nada no cache. No Lobby, troque o `getattr(identidade_client,
"_lembrar_sessao_valida", None)` de `apps/contas/views.py` pela chamada direta a
`identidade_client.lembrar_sessao_valida(usuario)`: o apelido sai na próxima
versão. O Formulário passa pelo `BackendIdentidade` e não tem o que trocar.

## Quem vem direto da 1.2 (Formulário e Lobby)

Além do que mudou na 1.5:
- 1.3: `TokenInvalido.motivo`, acima;
- 1.4: o IP do usuário conta da direita do `X-Forwarded-For`, e o app diz
  quantos proxies tem. Quem já reescreve o `REMOTE_ADDR` com o IP real por
  middleware próprio (o `RealIPMiddleware` do Formulário e do Lobby) põe
  `IDENTIDADE_NUM_PROXIES = 0`; sem a chave, o cliente lê o primeiro item e
  avisa no log;
- 1.5: o código do app que chama uma das escritas que saíram (a tela de
  usuários, a criação do admin mestre, o "revogar ao desativar", o link de
  senha emitido pelo app) sai junto: com a 1.5 ele quebra em `AttributeError`.
"""
import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils.module_loading import import_string

logger = logging.getLogger("identidade_client")

VERSAO = "1.5.3"


# === IP do usuário ========================================================
_AVISOU_SEM_PROXIES = False


def _proxies_confiados():
    """Quantos proxies o app diz ter na frente, ou `None` se não disse."""
    explicito = getattr(settings, "IDENTIDADE_NUM_PROXIES", None)
    if explicito is not None:
        return int(explicito)
    do_drf = int((getattr(settings, "REST_FRAMEWORK", {}) or {}).get("NUM_PROXIES") or 0)
    return do_drf or None


def ip_do_request(request):
    """IP do usuário final, para a auditoria e o bloqueio por origem (1.4).

    Cada proxy ACRESCENTA ao `X-Forwarded-For` o que recebeu, sem apagar o que
    veio antes: o começo da lista é o que o cliente escreveu, e só o N-ésimo a
    partir do fim foi posto por um proxy em que o app confia. Com N proxies,
    é esse o IP. Com `0`, não há proxy e vale o `REMOTE_ADDR`.

    Sem configuração nenhuma, mantém o comportamento de até a 1.3 (o primeiro
    item) para não trocar de repente o IP de um app que ainda não disse quantos
    proxies tem: com o `REMOTE_ADDR` do proxy, o bloqueio por IP viraria
    bloqueio de todo mundo. Esse modo é forjável, e o log avisa.
    """
    global _AVISOU_SEM_PROXIES
    if request is None:
        return None
    proxies = _proxies_confiados()
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
    enderecos = [e.strip() for e in encaminhado.split(",") if e.strip()]
    if proxies is None:
        if enderecos:
            if not _AVISOU_SEM_PROXIES:
                _AVISOU_SEM_PROXIES = True
                logger.warning(
                    "identidade_client: IDENTIDADE_NUM_PROXIES não configurado; "
                    "o IP enviado ao Conecta ID sai do começo do X-Forwarded-For, "
                    "que o cliente pode forjar"
                )
            return enderecos[0]
        return request.META.get("REMOTE_ADDR") or None
    if proxies and enderecos:
        return enderecos[-min(proxies, len(enderecos))]
    return request.META.get("REMOTE_ADDR") or None


# === Erros ================================================================
class ErroIdentidade(Exception):
    """Base. O app pode capturar só esta para tratar tudo de uma vez."""


class CredencialInvalida(ErroIdentidade):
    """E-mail ou senha incorretos — ou conta inativa, ou sem senha definida.

    O serviço não distingue os quatro casos de propósito, e o cliente também
    não deve tentar adivinhar: repassar a mensagem genérica é o comportamento
    correto.
    """


class SemAcessoAoApp(ErroIdentidade):
    """A senha confere, mas esta conta não tem acesso a este sistema."""


class BloqueadoTemporariamente(ErroIdentidade):
    """Excesso de tentativas. Mensagem para o usuário: tente mais tarde."""


class SenhaFraca(ErroIdentidade):
    """A senha nova não passou na política. `detalhe` explica o quê."""


class TokenInvalido(ErroIdentidade):
    """Link de senha inexistente, expirado, substituído ou já usado.

    `motivo` (desde a 1.3): `"substituido"`, `"expirado"`, `"usado"` ou `None`.
    `None` é o caso a tratar com a mensagem genérica — inclui o token inventado,
    e a tela não deve distinguir esse de nada.
    """

    SUBSTITUIDO = "substituido"
    EXPIRADO = "expirado"
    USADO = "usado"

    def __init__(self, mensagem="", motivo=None):
        super().__init__(mensagem)
        self.motivo = motivo


class EmailEmUso(ErroIdentidade):
    """Era o erro do `criar` e do `alterar`, que saíram na 1.5.

    Fica para o `except EmailEmUso` que ainda exista num app não quebrar em
    `AttributeError` no meio do tratamento de outro erro.
    """

    def __init__(self, mensagem, identidade_id=None):
        super().__init__(mensagem)
        self.identidade_id = identidade_id


class NaoEncontrado(ErroIdentidade):
    """Não há identidade com esse e-mail (ou id) liberada para este app.

    Erro próprio, e não `IdentidadeIndisponivel`: é uma resposta legítima do
    serviço sobre os dados enviados, não uma falha de infraestrutura. Tratar os
    dois como a mesma coisa faria a tela dizer "tente novamente em instantes"
    para quem digitou um e-mail que não existe — e a pessoa tentaria de novo,
    para sempre.
    """


class IdentidadeIndisponivel(ErroIdentidade):
    """O Conecta ID não respondeu, ou respondeu 5xx.

    **Este erro nunca pode virar "senha incorreta" na tela.** Se o serviço cai
    e todo mundo vê "senha incorreta" ao mesmo tempo, a leitura natural é
    vazamento de credenciais — e vem uma enxurrada de trocas de senha que não
    resolve nada. A tela certa é "sistema de login indisponível, tente em
    instantes".
    """


class IdentidadeEmManutencao(IdentidadeIndisponivel):
    """O Conecta está em manutenção (503 `erro: manutencao`, desde a 1.5).

    É uma `IdentidadeIndisponivel`, de propósito: quem só conhece a 1.4 continua
    mostrando "indisponível", e nunca "senha incorreta". Quem conhece a 1.5 a
    captura antes e mostra "Temporariamente indisponível / Em manutenção".

    - `mensagem`: o texto do aviso de manutenção publicado no Conecta, para a
      tela; sem texto no aviso, o `detalhe` genérico da resposta;
    - `previsao`: quando a manutenção deve acabar, `datetime` com fuso (o de
      Brasília), ou `None` quando não há previsão.
    """

    def __init__(self, detalhe="", mensagem=None, previsao=None):
        super().__init__(detalhe)
        self.mensagem = mensagem or detalhe
        self.previsao = previsao


_ERROS = {
    "credencial_invalida": CredencialInvalida,
    "sem_acesso_ao_app": SemAcessoAoApp,
    "bloqueado_temporariamente": BloqueadoTemporariamente,
    "senha_fraca": SenhaFraca,
    "token_invalido": TokenInvalido,
    "nao_encontrado": NaoEncontrado,
    "indisponivel": IdentidadeIndisponivel,
}


def _texto(valor):
    return valor.strip() if isinstance(valor, str) and valor.strip() else None


def _data_e_hora(valor):
    """A `previsao` da manutenção (ISO 8601 com fuso), ou `None` se não vier ou não for data."""
    if not isinstance(valor, str):
        return None
    try:
        quando = parse_datetime(valor)
    except ValueError:
        return None
    # Sem fuso não se sabe que hora é, e um `localtime()` na tela daria 500.
    return quando if quando is not None and quando.tzinfo is not None else None


def _formato_inesperado(chamada):
    """Resposta 2xx fora do contrato: falha de integração, nunca "senha incorreta" (1.5)."""
    logger.error("Conecta ID respondeu %s fora do formato do contrato", chamada)
    raise IdentidadeIndisponivel("Resposta inesperada do serviço de identidade.")


# === Cliente HTTP =========================================================
def _origem(url):
    """`(esquema, host, porta)` de uma URL, ou `None` se ela não disser para onde vai (1.5.3)."""
    try:
        partes = urllib.parse.urlsplit(url)
        esquema = partes.scheme.lower()
        porta = partes.port or {"http": 80, "https": 443}.get(esquema)
    except (AttributeError, TypeError, ValueError):
        # Porta que não é número, ou o que nem é texto: não é o mesmo destino de nada.
        return None
    if not esquema or not partes.hostname:
        return None
    return esquema, partes.hostname, porta


def _mesmo_destino(uma, outra):
    """As duas URLs vão ao mesmo lugar: mesmo esquema, host e porta (1.5.3)."""
    origem = _origem(uma)
    return origem is not None and origem == _origem(outra)


class _RedirecionamentoSemAChave(urllib.request.HTTPRedirectHandler):
    """Segue o 3xx como o `urllib`, mas a chave do app só vai ao mesmo destino (1.5.3).

    O `redirect_request` do `urllib` copia para o pedido novo todo cabeçalho posto
    por `add_header`, sem olhar o host. O `User-Agent` segue; o `Authorization`, só
    se o destino tiver o mesmo esquema, host e porta do pedido que foi redirecionado.
    E um 3xx que venha do outro destino não é seguido: quem responde lá não escolhe
    o caminho de volta, e a resposta dele já não vale (`_conferir_destino`).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if getattr(req, "_fora_do_destino", False):
            # `None` faz o `urllib` levantar o `HTTPError` do 3xx, com a URL de fora.
            return None
        novo = super().redirect_request(req, fp, code, msg, headers, newurl)
        if novo is not None and not _mesmo_destino(novo.full_url, req.full_url):
            novo.remove_header("Authorization")
            novo._fora_do_destino = True
        return novo


_ABRIDOR = None


def _abrir(requisicao, timeout):
    """O `urlopen` do cliente: o do `urllib`, com o `_RedirecionamentoSemAChave` (1.5.3).

    Montado uma vez por processo, como o do `urlopen`: o `HTTPSHandler` carrega os
    certificados da máquina ao nascer, e um por chamada custaria isso a cada
    pergunta ao Conecta.
    """
    global _ABRIDOR
    if _ABRIDOR is None:
        _ABRIDOR = urllib.request.build_opener(_RedirecionamentoSemAChave)
    return _ABRIDOR.open(requisicao, timeout=timeout)


class ClienteIdentidade:
    def __init__(self, url=None, chave=None, timeout=None):
        self.url = (url or getattr(settings, "IDENTIDADE_URL", "")).rstrip("/")
        self.chave = chave or getattr(settings, "IDENTIDADE_APP_KEY", "")
        # 5s é generoso: a chamada mais cara é um Argon2, na casa dos 60ms. Um
        # timeout largo aqui viraria fila de gunicorn presa esperando um
        # serviço que já caiu.
        self.timeout = timeout or getattr(settings, "IDENTIDADE_TIMEOUT", 5)

    # --- chamadas ---------------------------------------------------------
    def verificar(self, login, senha, ip=None):
        """Devolve os dados da identidade ou levanta o erro correspondente."""
        corpo = {"login": login, "senha": senha}
        if ip:
            corpo["ip"] = ip
        dados = self._pedir("POST", "/api/v1/credenciais/verificar", corpo)
        if not isinstance(dados, dict) or not dados.get("identidade_id"):
            _formato_inesperado("verificar")
        return dados

    def buscar_por_email(self, email):
        achados = self._pedir(
            "GET", "/api/v1/identidades?email=" + urllib.parse.quote(email)
        )
        if achados is not None and not isinstance(achados, list):
            _formato_inesperado("buscar_por_email")
        return achados[0] if achados else None

    def listar_com_acesso(self):
        """Quem tem acesso ativo a ESTE app, com a configuração de cada um.

        É o que permite a tela de usuários mostrar quem foi liberado no Conecta
        ID e ainda não entrou — gente que, sem esta chamada, não aparece em
        lugar nenhum do app. Era esse buraco que fazia alguém criar a pessoa de
        novo, e a segunda conta nascer.
        """
        lista = self._pedir("GET", "/api/v1/identidades?com_acesso=1") or []
        if not isinstance(lista, list):
            _formato_inesperado("listar_com_acesso")
        return lista

    def obter(self, identidade_id):
        dados = self._pedir("GET", f"/api/v1/identidades/{identidade_id}")
        if not isinstance(dados, dict):
            _formato_inesperado("obter")
        return dados

    def esqueci_senha(self, email):
        """Pede o link de senha e deixa o Conecta ID mandar por e-mail.

        É o que a tela pública "esqueci minha senha" do app chama. Não devolve
        nada e não distingue e-mail que existe de e-mail que não existe — a
        resposta é 202 para qualquer entrada, de propósito. O app deve mostrar
        sempre a mesma frase ("se houver conta nesse endereço, o link foi
        enviado"); qualquer mensagem mais específica reconstrói, na tela, a
        distinção que a API se recusa a fazer.

        Rota `api/v1/senha/esqueci` do Conecta, desde a TSK-1217.
        """
        self._pedir("POST", "/api/v1/senha/esqueci", {"email": email})

    def definir_senha(self, token, senha_nova):
        """Define a senha pelo link do e-mail (`api/v1/senha/definir`, desde a TSK-1217).

        `TokenInvalido` (com o `motivo`, desde a 1.3) para o link que não vale,
        e `SenhaFraca` para a senha que não passa na política.
        """
        self._pedir("POST", "/api/v1/senha/definir", {"token": token, "senha_nova": senha_nova})

    def trocar_senha(self, identidade_id, senha_atual, senha_nova, ip=None):
        """Troca a senha de quem está logado (`api/v1/identidades/<id>/senha`, desde a TSK-1217).

        `CredencialInvalida` para a senha atual errada, `BloqueadoTemporariamente`
        depois de tentativas demais, `SenhaFraca` para a nova e `NaoEncontrado`
        para a identidade que este app não enxerga.
        """
        corpo = {"senha_atual": senha_atual, "senha_nova": senha_nova}
        if ip:
            corpo["ip"] = ip
        self._pedir("POST", f"/api/v1/identidades/{identidade_id}/senha", corpo)

    def saudavel(self):
        """Para healthcheck do app: True se o Conecta ID responde.

        Em manutenção, False: o `/health` do Conecta também responde o 503.
        """
        try:
            self._pedir("GET", "/health", autenticado=False)
            return True
        except ErroIdentidade:
            return False

    # --- transporte -------------------------------------------------------
    def _pedir(self, metodo, caminho, corpo=None, autenticado=True):
        if not self.url:
            raise IdentidadeIndisponivel("IDENTIDADE_URL não configurada.")

        dados = json.dumps(corpo).encode() if corpo is not None else None
        try:
            requisicao = urllib.request.Request(self.url + caminho, data=dados, method=metodo)
        except ValueError as erro:
            # `IDENTIDADE_URL` sem `https://`: até a 1.4, o login dava 500.
            logger.error("IDENTIDADE_URL inválida: %r", self.url)
            raise IdentidadeIndisponivel("IDENTIDADE_URL inválida.") from erro
        requisicao.add_header("Content-Type", "application/json")
        # A Cloudflare barra o `Python-urllib/3.x` do urllib (1.5.2). O `add_header`,
        # e não o `add_unredirected_header`, para o nome seguir num redirecionamento.
        requisicao.add_header("User-Agent", f"ConectaIdentidadeClient/{VERSAO}")
        if autenticado:
            if not self.chave:
                raise IdentidadeIndisponivel("IDENTIDADE_APP_KEY não configurada.")
            # Também por `add_header`, para seguir um 3xx para o mesmo destino: o
            # `_RedirecionamentoSemAChave` a tira do pedido que vai a outro (1.5.3).
            requisicao.add_header("Authorization", self.chave)

        try:
            with _abrir(requisicao, self.timeout) as resposta:
                # Antes do corpo: o de outro destino nem é lido.
                self._conferir_destino(resposta.url, caminho)
                bruto = resposta.read()
        except urllib.error.HTTPError as erro:
            self._conferir_destino(erro.url, caminho)
            self._traduzir(erro)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError,
                ValueError) as erro:
            # Rede caiu, DNS não resolveu, timeout, conexão cortada no meio da
            # resposta, ou um 3xx com um `Location` que nem é URL (o `ValueError`
            # do `urllib`, desde a 1.5.3; antes, 500 na tela de login). Nunca
            # confundir com senha errada: quem decide o que mostrar é o app, e ele
            # precisa saber a diferença.
            logger.error("Conecta ID inalcançável em %s%s: %s", self.url, caminho, erro)
            raise IdentidadeIndisponivel("Serviço de identidade inalcançável.") from erro

        if not bruto:
            return None
        try:
            return json.loads(bruto)
        except ValueError as erro:
            # A página HTML de um proxy, ou um IDENTIDADE_URL que cai fora da
            # API: não é resposta do serviço (1.5).
            logger.error("Conecta ID respondeu %s%s com algo que não é JSON", self.url, caminho)
            raise IdentidadeIndisponivel("Resposta inesperada do serviço de identidade.") from erro

    def _conferir_destino(self, destino, caminho):
        """A resposta veio de onde o pedido foi? Se um 3xx a levou para fora, não vale (1.5.3).

        O pedido redirecionado para outro destino saiu sem a chave, e o que voltou
        de lá não é resposta do Conecta: nem o erro (um `credencial_invalida` de
        outro host viraria "senha incorreta"), nem o 200 (viraria login).
        """
        if _mesmo_destino(destino, self.url):
            return
        origem = _origem(destino)
        logger.error(
            "Conecta ID redirecionou %s%s para outro destino (%s); a chave do app não foi "
            "junto, e a resposta de lá foi descartada",
            self.url, caminho, "%s://%s:%s" % origem if origem else "ilegível",
        )
        raise IdentidadeIndisponivel("O serviço de identidade redirecionou para outro destino.")

    def _traduzir(self, erro_http):
        try:
            corpo = json.loads(erro_http.read().decode())
        except Exception:
            corpo = {}
        if not isinstance(corpo, dict):
            corpo = {}
        codigo = corpo.get("erro")
        if not isinstance(codigo, str):
            codigo = ""
        detalhe = corpo.get("detalhe", "Falha ao falar com o serviço de identidade.")

        if codigo == "email_em_uso":
            raise EmailEmUso(detalhe, corpo.get("identidade_id"))
        if codigo == "token_invalido":
            raise TokenInvalido(detalhe, corpo.get("motivo") or None)
        if codigo == "manutencao":
            # Prevista, e não erro de integração: aviso, e não erro, no log.
            logger.warning("Conecta em manutenção (HTTP %s): %s", erro_http.code, detalhe)
            raise IdentidadeEmManutencao(
                detalhe,
                mensagem=_texto(corpo.get("mensagem")),
                previsao=_data_e_hora(corpo.get("previsao")),
            )

        classe = _ERROS.get(codigo)
        if classe:
            raise classe(detalhe)

        # `app_nao_autorizado`, `sem_permissao`, `dados_invalidos` e qualquer
        # 5xx caem aqui. São erros de integração, não do usuário: quem errou foi
        # o app, e o log precisa dizer isso alto.
        logger.error(
            "Conecta ID recusou a chamada (HTTP %s, erro=%s): %s",
            erro_http.code, codigo or "?", detalhe,
        )
        raise IdentidadeIndisponivel(detalhe)


# === Backend de autenticação do Django ====================================
def central_ativa():
    """A chave que reverte tudo, sem migration e sem deploy de emergência."""
    return bool(getattr(settings, "AUTH_CENTRAL_ATIVO", False))


class BackendIdentidade(BaseBackend):
    """Autentica no Conecta ID e devolve o usuário local correspondente.

    Fica **antes** do `ModelBackend` na `AUTHENTICATION_BACKENDS`. Com
    `AUTH_CENTRAL_ATIVO=False` ele sai da frente sem fazer nada, e o login
    volta a ser o local — é assim que se desliga a integração em segundos.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            # Como o `ModelBackend`: `authenticate(email=...)` num app com
            # `USERNAME_FIELD = "email"`. Sem isto, a chamada pulava o login
            # central e ia direto à senha local do fallback (1.5).
            from django.contrib.auth import get_user_model

            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if not central_ativa() or not username or not password:
            return None

        cliente = ClienteIdentidade()
        try:
            dados = cliente.verificar(username, password, ip=self._ip(request))
        except (CredencialInvalida, SemAcessoAoApp):
            if _conta_ligada(username):
                # Desde a 1.5: a conta ligada a uma identidade só entra pelo
                # login central. Sem isto, o ModelBackend de fallback tentaria a
                # senha local dela, e quem foi desativado ou perdeu o acesso no
                # Conecta entraria com uma senha antiga do app. O
                # `PermissionDenied` encerra a fila de backends, e o
                # `authenticate()` do Django devolve None, como antes.
                raise PermissionDenied
            # `None` faz o Django seguir para o próximo backend (a conta local,
            # como o superusuário de emergência do Selo) e, no fim, responder
            # "credenciais inválidas" — que é o certo aqui.
            return None
        # BloqueadoTemporariamente e IdentidadeIndisponivel (com a
        # IdentidadeEmManutencao) sobem de propósito: a view precisa distinguir
        # "tente mais tarde" e "em manutenção" de "senha errada".

        resolver = import_string(settings.IDENTIDADE_RESOLVER_USUARIO)
        usuario = resolver(dados)
        if usuario is None:
            logger.error(
                "identidade %s autenticou mas o app não resolveu o usuário local",
                dados.get("identidade_id"),
            )
            if _conta_ligada(username):
                # A conta deste login é de outra identidade (ver
                # `resolver_com_vinculo`): a senha local dela também não entra.
                raise PermissionDenied
            return None
        if not self.user_can_authenticate(usuario):
            # Desde a 1.5: a conta deste app, desativada aqui, não entra só
            # porque a identidade está ativa no Conecta.
            logger.info(
                "identidade %s autenticou, mas a conta local está inativa",
                dados.get("identidade_id"),
            )
            return None

        # `precisa_trocar_senha` vem do Conecta ID; a view do app decide o que
        # fazer com ele (banner, redirecionamento).
        usuario.identidade_precisa_trocar_senha = dados.get("precisa_trocar_senha", False)
        # O Conecta acabou de dizer que a identidade vale neste app.
        lembrar_sessao_valida(usuario)
        return usuario

    def get_user(self, user_id):
        from django.contrib.auth import get_user_model

        try:
            usuario = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            return None
        return usuario if self.user_can_authenticate(usuario) else None

    def user_can_authenticate(self, usuario):
        """Conta local inativa não entra, como no `ModelBackend` (1.5).

        Até a 1.4, o `get_user` devolvia o usuário desativado no app, e a
        sessão aberta dele seguia valendo.
        """
        return getattr(usuario, "is_active", True)

    @staticmethod
    def _ip(request):
        """IP do usuário final: ver `ip_do_request` (conta da direita desde a 1.4)."""
        return ip_do_request(request)


def _conta_ligada(login):
    """Há conta local com este login (e-mail ou `USERNAME_FIELD`) ligada a uma identidade? (1.5)

    Sem o vínculo `vinculo_identidade` no modelo, ou com o banco fora, False: o
    login segue para o próximo backend, como até a 1.4.
    """
    from django.contrib.auth import get_user_model
    from django.core.exceptions import FieldError

    Usuario = get_user_model()
    try:
        filtro = Q(**{Usuario.USERNAME_FIELD: login}) | Q(email__iexact=login)
        return Usuario._default_manager.filter(filtro, vinculo_identidade__isnull=False).exists()
    except FieldError:
        return False
    except Exception:
        logger.warning("falha ao procurar a conta local de um login recusado", exc_info=True)
        return False


# === Revalidação da sessão (1.5) ==========================================
#: No máximo uma pergunta ao Conecta por usuário neste intervalo, em segundos.
REVALIDAR_A_CADA = 5 * 60
#: Depois de uma falha do Conecta, ninguém pergunta por este tempo, em segundos.
PAUSA_DEPOIS_DE_FALHA = 60
#: Teto do timeout da pergunta, em segundos: abaixo do do login, que é de 5.
TIMEOUT_DA_REVALIDACAO = 2

#: Por quanto tempo a última resposta clara fica guardada, em segundos.
GUARDAR_A_ULTIMA_RESPOSTA = 24 * 60 * 60

_VALIDA = "valida"
_RECUSADA = "recusada"
_ULTIMA = ":ultima"
#: Ao lado da resposta (1.5.3): a hora do último login aceito e a do começo da pergunta que deu a
#: última recusa. Cada uma tem um escritor só (o login, a recusa). Ver `_recusada`.
_LOGIN = ":login"
_RECUSA = ":recusa"
_AVISOU_SEM_VINCULO = False
_ULTIMO_REGISTRO_DE_FALHA = None


def revalidar_sessao(usuario):
    """A sessão já aberta de `usuario` ainda vale? Nunca levanta (1.5).

    Pergunta ao Conecta (`obter`) e devolve False só com uma resposta clara de
    que a identidade não vale mais neste app:

    - `"ativo": false` na resposta: a pessoa não está ativa no Conecta. É o
      `ativo` do `IdentidadeCompleta` da API, que desde a TSK-1253 é o
      `esta_ativa`: falso com a identidade desativada e também com o `Usuario`
      ligado a ela inativo ou bloqueado no Conecta;
    - 404 `nao_encontrado`: a identidade não é visível para este app. Desde a
      TSK-1253, é como a API diz, a todo app (com ou sem
      `pode_gerir_identidades`), "sem acesso ativo a este app" (ou "não existe
      mais"): o acesso revogado derruba a sessão. Um `sem_acesso_ao_app`
      diria o mesmo.

    True em todo o resto: Conecta fora do ar, em manutenção, chave ou IP
    recusados, limite estourado, resposta estranha, cache fora, bug aqui dentro.
    Sessão derrubada por falha do Conecta seria todo mundo dos cinco apps fora
    ao mesmo tempo. A falha só não desfaz uma recusa: a última resposta clara
    fica guardada (`GUARDAR_A_ULTIMA_RESPOSTA`) e vale enquanto o Conecta não
    der outra, inclusive para a requisição que chega com a pergunta no ar. Com o
    Conecta pendurado, cada pergunta seguraria um worker:
    por isso o timeout dela é de no máximo `TIMEOUT_DA_REVALIDACAO` segundos, e
    depois de uma falha ninguém pergunta por `PAUSA_DEPOIS_DE_FALHA` segundos.
    A falha inesperada (cache fora, bug) vai ao log no máximo uma vez nesse
    mesmo intervalo, por processo: uma por requisição inundaria o log e o
    GlitchTip enquanto o Redis estiver fora. E True sem perguntar nada
    com `AUTH_CENTRAL_ATIVO` desligado (a chave que reverte tudo), para quem não
    está autenticado e para a conta local, sem vínculo (as empresas do Selo).

    No máximo uma pergunta por usuário a cada `REVALIDAR_A_CADA` segundos, com a
    resposta guardada no cache padrão do Django: a recusa também fica guardada,
    e um token velho reapresentado não volta a valer nem vira uma chamada por
    requisição. Com o LocMem (o padrão de quem não configura `CACHES`) a conta é
    por processo: com três workers, até três perguntas por pessoa a cada 5
    minutos. O login aceito vale como revalidação (no processo que o atendeu):
    o `BackendIdentidade` chama `lembrar_sessao_valida`, e o app que chama o
    `verificar` direto chama ela também. As perguntas entram na cota de leitura
    do app no Conecta (120 por minuto): com LocMem, cerca de pessoas ativas ×
    workers ÷ 5 por minuto. Estourada, a cota dá 429, e a sessão fica de pé.

    Desde a 1.5.3, a recusa da pergunta que começou antes do último login aceito
    não vale (a hora do login é a da chamada a `lembrar_sessao_valida`, depois
    de o Conecta o aceitar): ela nem é gravada, e a requisição dela recebe o
    login; se for gravada mesmo assim, a leitura a descarta. Ver `_recusada`.

    O `identidade_id` sai de `usuario.vinculo_identidade` (o vínculo de
    `resolver_com_vinculo`), e tem de ser o que o Conecta conhece: um vínculo
    com o id de outra base dá 404 e derruba a sessão.
    """
    try:
        if not central_ativa() or not getattr(usuario, "is_authenticated", False):
            return True
        chave = _chave_da_sessao(usuario)
        anterior = cache.get(chave)
        if anterior is not None:
            return not _recusada(chave, anterior)
        # A última resposta clara vale até chegar outra: é o que recebe a
        # requisição que chega com a pergunta no ar, e o que fica se o Conecta
        # não responder. Sem ela, uma recusa vencida abria uma janela a cada 5
        # minutos para um token velho.
        ultima = cache.get(chave + _ULTIMA)
        if cache.get(_chave_da_pausa()) is not None:
            return not _recusada(chave, ultima)
        if not cache.add(chave, ultima or _VALIDA, REVALIDAR_A_CADA):
            # Outra requisição desta pessoa pegou a vez entre o `get` e o `add`.
            return not _recusada(chave, cache.get(chave))
        identidade_id = _identidade_de(usuario)
        if identidade_id is None:
            return True
        # A marca da resposta é o INÍCIO da pergunta (1.5.3): um login aceito
        # enquanto ela está no ar é mais novo do que ela.
        inicio = time.time()
        resposta = _resposta_do_conecta(identidade_id)
        if resposta is None:
            # Relida, e não a do começo: um login aceito com a pergunta no ar
            # é mais novo que a recusa que estava lá.
            return not _recusada(chave, cache.get(chave + _ULTIMA))
        return _guardar_resposta(chave, resposta, inicio) != _RECUSADA
    except Exception:
        _registrar_falha()
        return True


def _registrar_falha():
    """Põe a falha inesperada no log, no máximo uma vez a cada `PAUSA_DEPOIS_DE_FALHA` segundos."""
    global _ULTIMO_REGISTRO_DE_FALHA
    agora = time.time()
    if (_ULTIMO_REGISTRO_DE_FALHA is not None
            and 0 <= agora - _ULTIMO_REGISTRO_DE_FALHA < PAUSA_DEPOIS_DE_FALHA):
        return
    _ULTIMO_REGISTRO_DE_FALHA = agora
    logger.exception("falha ao revalidar a sessão; ela fica de pé")


def _resposta_do_conecta(identidade_id):
    """`_VALIDA`, `_RECUSADA`, ou `None` quando o Conecta não responde com clareza."""
    timeout = min(TIMEOUT_DA_REVALIDACAO, getattr(settings, "IDENTIDADE_TIMEOUT", None) or 5)
    try:
        dados = ClienteIdentidade(timeout=timeout).obter(identidade_id)
    except (NaoEncontrado, SemAcessoAoApp):
        logger.warning(
            "sessão encerrada: a identidade %s não tem acesso ativo a este app, ou o "
            "login central não a conhece (vínculo com o id de outra base?)", identidade_id
        )
        return _RECUSADA
    except ErroIdentidade as erro:
        cache.set(_chave_da_pausa(), True, PAUSA_DEPOIS_DE_FALHA)
        logger.warning(
            "sessão mantida: o Conecta não confirmou a identidade %s (%s: %s)",
            identidade_id, type(erro).__name__, erro,
        )
        return None
    ativo = dados.get("ativo") if isinstance(dados, dict) else None
    if ativo is False:
        logger.warning(
            "sessão encerrada: a identidade %s está desativada, ou o usuário do Conecta "
            "ligado a ela está inativo ou bloqueado", identidade_id
        )
        return _RECUSADA
    return _VALIDA if ativo is True else None


def _identidade_de(usuario):
    """O `identidade_id` do vínculo do usuário, ou `None` para a conta local."""
    global _AVISOU_SEM_VINCULO
    try:
        vinculo = usuario.vinculo_identidade
    except ObjectDoesNotExist:
        # O `RelatedObjectDoesNotExist` do Django, que é também AttributeError:
        # este `except` tem de vir antes do outro.
        return None
    except AttributeError:
        if not _AVISOU_SEM_VINCULO:
            _AVISOU_SEM_VINCULO = True
            logger.warning(
                "identidade_client: o usuário não tem `vinculo_identidade`; "
                "revalidar_sessao não confere ninguém neste app"
            )
        return None
    return getattr(vinculo, "identidade_id", None)


def _app_da_chave():
    """O slug do app no `IDENTIDADE_APP_KEY` (`AppKey <slug>:<segredo>`), nunca o segredo."""
    partes = (getattr(settings, "IDENTIDADE_APP_KEY", "") or "").split()
    slug, separador, _ = (partes[-1] if partes else "").partition(":")
    return slug if separador else ""


def _chave_da_sessao(usuario):
    """Por app e por usuário: dois apps no mesmo Redis não trocam a resposta de um pelo outro."""
    modelo = getattr(getattr(usuario, "_meta", None), "label_lower", "usuario")
    return f"identidade_client:sessao:{_app_da_chave()}:{modelo}:{usuario.pk}"


def _chave_da_pausa():
    return f"identidade_client:pausa:{_app_da_chave()}"


def lembrar_sessao_valida(usuario):
    """O login que a API acabou de aceitar vale como revalidação, e apaga uma recusa antiga (1.5.3).

    Quando chamar: logo depois de o `verificar` devolver a identidade e o app
    resolver o usuário local, antes de emitir a sessão ou o token. O
    `BackendIdentidade` já chama; quem chama o `verificar` direto, sem ele (o
    Lobby e o Selo), chama esta. Só aí: ela não pergunta nada ao Conecta, e
    chamada em outro ponto (na renovação do token, num login recusado) daria por
    conferida uma sessão que ninguém conferiu.

    Sem isso, quem foi reativado minutos depois de ter a sessão derrubada
    entraria e cairia de novo, pela recusa ainda no cache. O login vale da hora
    desta chamada, na regra de ordem da `revalidar_sessao`: a pergunta ao Conecta
    que começou antes dela, inclusive durante o `verificar`, não grava a recusa
    por cima. É de propósito, e não a hora do começo do `verificar`: com esta, a
    pergunta que saiu durante o login e leu a pessoa ainda inativa venceria o
    login da pessoa reativada, que entraria e cairia na hora. O preço é o
    contrário, e menor: quem é desativado durante o próprio login fica até a
    pergunta seguinte, em 5 minutos, a demora que a desativação já tem. Com o
    LocMem, vale só no processo que atendeu o login: nos outros, a recusa dura o
    resto dos 5 minutos. Nunca levanta.
    """
    try:
        chave = _chave_da_sessao(usuario)
        # A hora do login antes da resposta: quem vir a resposta vê a hora.
        cache.set(chave + _LOGIN, time.time(), GUARDAR_A_ULTIMA_RESPOSTA)
        cache.set(chave, _VALIDA, REVALIDAR_A_CADA)
        cache.set(chave + _ULTIMA, _VALIDA, GUARDAR_A_ULTIMA_RESPOSTA)
    except Exception:
        logger.warning("falha ao guardar a revalidação do login", exc_info=True)


#: O nome da 1.5.2, que o Lobby chama por `getattr`. Apelido só nesta versão: sai
#: na próxima. Use `lembrar_sessao_valida`.
_lembrar_sessao_valida = lembrar_sessao_valida


def _marca_de(guardada):
    """A hora guardada ao lado da resposta, ou `None` sem ela."""
    try:
        return float(guardada)
    except (TypeError, ValueError):
        return None


def _recusada(chave, guardada):
    """A resposta guardada em `chave` é uma recusa que ainda vale? (1.5.3)

    Não vale a recusa da pergunta que começou antes do último login aceito: o
    Conecta aceitou o login depois de ela ter lido o estado da pessoa. É a
    leitura que decide, e não a ordem em que as gravações caem no cache: a
    recusa velha que grava depois do login (outro processo no mesmo Redis, uma
    pausa no meio da gravação) não vale do mesmo jeito. Sem a hora do login ou
    sem a da recusa (a 1.5.2 gravou a recusa, ou o cache cortou a chave), a
    recusa vale, como até a 1.5.2. No empate, também vale.
    """
    if guardada != _RECUSADA:
        return False
    login = _marca_de(cache.get(chave + _LOGIN))
    recusa = _marca_de(cache.get(chave + _RECUSA))
    return login is None or recusa is None or recusa >= login


def _guardar_resposta(chave, resposta, inicio):
    """Grava a resposta da pergunta que começou em `inicio`, e devolve a que vale (1.5.3).

    A recusa só é gravada se a pergunta não for mais velha que o último login
    aceito; senão, vale o login. A resposta continua o texto puro da 1.5.2, e a
    hora vai numa chave ao lado, que a 1.5.2 não lê: a imagem que volta para a
    1.5.2 (o rollback), num Redis, lê a recusa gravada aqui como recusa.

    Sem trava, porque a API de cache do Django não tem compare-and-set: se o
    login gravar entre a conferência e a gravação da recusa, a `_recusada`
    ainda a descarta na leitura, pela hora. Por isso a hora da recusa vai antes
    da resposta, e a devolvida é conferida de novo depois de gravar.
    """
    if resposta == _RECUSADA:
        login = _marca_de(cache.get(chave + _LOGIN))
        if login is not None and login > inicio:
            return _VALIDA
        cache.set(chave + _RECUSA, inicio, GUARDAR_A_ULTIMA_RESPOSTA)
    cache.set(chave, resposta, REVALIDAR_A_CADA)
    cache.set(chave + _ULTIMA, resposta, GUARDAR_A_ULTIMA_RESPOSTA)
    return _RECUSADA if _recusada(chave, resposta) else _VALIDA


# === Provisionamento ======================================================
class Provisionamento:
    """A `config_do_app` lida com desconfiança.

    Nenhuma leitura levanta e toda leitura tem padrão, de propósito: a carga é
    digitada por gente no admin do Conecta ID e pode chegar com um papel que
    este app não conhece — porque foi renomeado aqui, ou porque a declaração de
    lá ficou para trás. O pior desfecho aceitável é a conta nascer no padrão do
    app e o log dizer por quê; recusar o login por causa de um valor estranho
    seria transformar um erro de cadastro em "não consigo entrar".
    """

    def __init__(self, dados):
        bruto = (dados or {}).get("config_do_app")
        self.bruto = bruto if isinstance(bruto, dict) else {}

    def __bool__(self):
        return bool(self.bruto)

    def texto(self, chave, padrao=""):
        valor = self.bruto.get(chave)
        return valor.strip() if isinstance(valor, str) and valor.strip() else padrao

    def booleano(self, chave, padrao=False):
        valor = self.bruto.get(chave)
        if isinstance(valor, bool):
            return valor
        if isinstance(valor, str):
            return valor.strip().lower() in {"1", "true", "sim", "verdadeiro"}
        return padrao

    def escolha(self, chave, opcoes, padrao=None):
        """O valor, se for uma das `opcoes`. Senão o padrão, com aviso no log.

        O aviso não é decoração: é a única pista de que alguém preencheu
        "Líder" onde o app espera "lider", e de que a conta nasceu observador
        por causa disso.
        """
        valor = self.texto(chave)
        if not valor:
            return padrao
        if valor in opcoes:
            return valor
        logger.warning(
            "provisionamento com %s=%r desconhecido neste app; usando %r",
            chave, valor, padrao,
        )
        return padrao


# === Usuário sombra =======================================================
def resolver_com_vinculo(
    dados, modelo_vinculo, ao_criar=None, sincronizar=True, ao_reaplicar=None
):
    """Devolve o usuário local correspondente à identidade, criando se preciso.

    `modelo_vinculo` é um modelo do PRÓPRIO app, com `identidade_id` (UUID
    único) e `usuario` (OneToOne). Cinco linhas, e é o app que as declara (o
    `related_name` é o que a `revalidar_sessao` lê, desde a 1.5):

        class VinculoIdentidade(models.Model):
            identidade_id = models.UUIDField(unique=True, db_index=True)
            usuario = models.OneToOneField(
                settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                related_name="vinculo_identidade")

    O casamento é por `identidade_id` primeiro e por e-mail depois, e o e-mail
    só casa com conta sem vínculo ou ligada a esta mesma identidade (1.5). A
    ordem importa: o e-mail muda (casamento, troca de sobrenome, correção de digitação
    — o Diego que o diga), o UUID não. Casar por e-mail primeiro faria uma
    correção de cadastro criar um usuário novo e órfão, com o histórico do
    antigo preso na conta velha.

    `ao_criar(dados)` é chamado quando não existe usuário local nenhum. É lá
    que o app decide o que a pessoa é dentro dele — papel no financeiro, nível
    de acesso e equipe no kanban. Se devolver `None`, ninguém entra: é o jeito
    de dizer "esta pessoa tem identidade, mas não tem lugar aqui".

    `dados["config_do_app"]` traz o que quem administra preencheu no Conecta ID
    sobre esta pessoa neste app; leia com `Provisionamento(dados)`. Ela é
    consumida **só aqui**, na criação: aplicá-la a cada login desfaria, sem
    avisar, toda edição feita na tela do próprio app.
    """
    identidade_id = dados["identidade_id"]
    email = (dados.get("email") or "").strip().lower()

    vinculo = modelo_vinculo.objects.filter(identidade_id=identidade_id).select_related(
        "usuario"
    ).first()
    if vinculo:
        usuario = vinculo.usuario
        _talvez_reaplicar(usuario, dados, ao_reaplicar)
        if sincronizar:
            _sincronizar(usuario, dados)
        return usuario

    Usuario = modelo_vinculo._meta.get_field("usuario").related_model
    usuario = Usuario.objects.filter(email__iexact=email).first() if email else None

    recem_criado = False
    if usuario is None:
        if ao_criar is None:
            return None
        usuario = ao_criar(dados)
        if usuario is None:
            return None
        recem_criado = True

    # `get_or_create` e não `create`: se dois requests da mesma pessoa chegarem
    # juntos no primeiro login, os dois tentam vincular ao mesmo usuário.
    vinculo, criado = modelo_vinculo.objects.get_or_create(
        usuario=usuario, defaults={"identidade_id": identidade_id}
    )
    if not criado and str(vinculo.identidade_id).lower() != str(identidade_id).lower():
        # Desde a 1.5: o e-mail casou com uma conta já ligada a OUTRA
        # identidade, e ninguém entra. Até a 1.4 a conta era entregue, e um
        # e-mail reaproveitado no login central (antes de a conta local
        # sincronizar o e-mail novo da pessoa anterior) dava à identidade nova
        # a conta e as permissões da antiga. Também é o caso da importação que
        # trocou os UUIDs, ou de um vínculo digitado à mão: o log diz qual
        # corrigir, e o cliente não o regrava sozinho.
        logger.error(
            "login recusado: o e-mail casou com o usuário %s, ligado à identidade "
            "%s, mas o login central autenticou a %s; corrija o vínculo",
            getattr(usuario, "pk", usuario), vinculo.identidade_id, identidade_id,
        )
        return None
    if not recem_criado:
        # Conta local que já existia e só agora ganhou vínculo — o caso de quem
        # foi casado pelo e-mail. Para ela a semente nunca foi aplicada, então a
        # reaplicação é o único jeito de a configuração chegar.
        _talvez_reaplicar(usuario, dados, ao_reaplicar)
    if sincronizar:
        _sincronizar(usuario, dados)
    return usuario


def _talvez_reaplicar(usuario, dados, ao_reaplicar):
    """Chama o `ao_reaplicar` do app se o Conecta ID pediu, e só então.

    A marca já foi consumida do lado de lá quando esta resposta foi montada: ela
    vale para este login e mais nenhum. Um app que não passe `ao_reaplicar`
    simplesmente ignora o pedido — e o efeito é o de antes desta versão existir,
    não um erro.
    """
    if ao_reaplicar is None or not (dados or {}).get("reaplicar_config"):
        return
    logger.info(
        "reaplicando a configuração do Conecta ID em %s",
        getattr(usuario, "email", usuario),
    )
    ao_reaplicar(usuario, dados)


def _sincronizar(usuario, dados):
    """Traz e-mail e nome do Conecta ID para o usuário local.

    O Conecta ID é a fonte para estes dois campos; o resto (papel, equipe,
    permissões) é do app e não se toca. Sem esta sincronização, corrigir um
    e-mail no lugar certo não teria efeito nenhum onde as pessoas olham.
    """
    campos = []
    email = (dados.get("email") or "").strip().lower()
    if email and getattr(usuario, "email", "") != email:
        usuario.email = email
        campos.append("email")

    nome = (dados.get("nome") or "").strip()
    if nome and hasattr(usuario, "nome") and usuario.nome != nome:
        usuario.nome = nome
        campos.append("nome")
    elif nome and hasattr(usuario, "first_name"):
        partes = nome.split()
        primeiro, ultimo = partes[0], " ".join(partes[1:])
        if usuario.first_name != primeiro or usuario.last_name != ultimo:
            usuario.first_name, usuario.last_name = primeiro, ultimo
            campos += ["first_name", "last_name"]

    if campos:
        usuario.save(update_fields=campos)
