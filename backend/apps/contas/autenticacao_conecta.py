"""
A asserção do Conecta — como o Conecta chama o Lobby em nome de uma pessoa.

O contrato é o ADR-002 (§§4, 8, 10 e a emenda do Lobby, §13). Em uma frase: o
servidor do Conecta assina, a cada chamada, um JWT curto em Ed25519 que diz
"quem pede isto é a pessoa de e-mail X, e vale por 60 segundos, só para o
Lobby". O Lobby confere a assinatura com a chave PÚBLICA e atende como aquela
pessoa — com o papel que ela tem AQUI.

O QUE ESTA ASSERÇÃO NÃO É

- Não é conta de serviço. Não há usuário "conecta" com poder nenhum: cada
  chamada é de uma pessoa, e o autor que fica no histórico é ela (ADR §3.2).
- Não carrega permissão. O Conecta diz QUEM é; quem diz o que a pessoa PODE é o
  Lobby, pelo `VinculoIdentidade.papel` (§2.3). Mandar papel na asserção seria
  dar ao Conecta o poder de promover alguém aqui dentro.
- Não cria usuário. O login (`identidade.resolver_usuario`) cria conta local no
  primeiro acesso; a asserção NUNCA passa por lá. Sem conta, 401 `SEM_CONTA`.
- Não é passe geral. Ela abre só as rotas de `ROTAS_LIBERADAS`, conferidas por
  `url_name` e método — e é declarada rota a rota (`AceitaAssercao`), não no
  `DEFAULT_AUTHENTICATION_CLASSES`. As rotas de escrita do Lobby mudam o preço de
  toda venda no instante seguinte (§13.2).

FALHA FECHADA

Sem chave pública configurada, ou sem cache compartilhado com `DEBUG=False`, a
integração se declara desligada: quem manda o cabeçalho recebe 503
`INTEGRACAO_DESLIGADA`. Quem NÃO manda — o consultor anônimo, a diretoria no
/admin de hoje — não percebe nada: a classe só age quando o cabeçalho existe.

O cache importa porque é nele que o `jti` fica guardado contra repetição
(§4.6). `LocMemCache` é por processo: com 3 workers do gunicorn, a mesma
asserção passaria até 3 vezes.

O QUE NUNCA VAI PARA O LOG

A asserção, inteira ou em pedaço. A linha de log de cada chamada leva `kid`,
`jti`, `sub`, `aud`, nome da url, método, status, `codigo`, tempo e IP (§10.3) —
o bastante para depurar um 401 sem guardar nada que sirva para repeti-lo.
"""
import base64
import json
import logging
import time
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.throttling import SimpleRateThrottle

from config.real_ip import ip_do_cliente

logger = logging.getLogger("lobby.integracao")

# O nome do cabeçalho, como o Django o expõe. Hífen no HTTP, nunca sublinhado: o
# nginx descarta por padrão cabeçalho com `_` (§4.1).
CABECALHO = "HTTP_X_CONECTA_ASSERCAO"
ESQUEMA = "Conecta-Assercao"

# Fixa no código, e não no .env: ver o bloco da asserção em `config/settings.py`.
AUDIENCIA = "lobby"

ALGORITMO = "EdDSA"
# `exp − iat` acima disto é recusado mesmo com assinatura boa: a validade é
# conferida dos dois lados, não só confiada ao emissor (§4.3).
VALIDADE_MAXIMA = 70
# Um JWT de asserção tem algumas centenas de bytes. Isto só barra lixo.
TAMANHO_MAXIMO = 4096

METODOS_DE_LEITURA = frozenset({"GET", "HEAD", "OPTIONS"})

# A lista fechada da §13.4, por `url_name` e método. Prefixo de caminho cresce
# sozinho quando alguém registra uma rota nova debaixo dele; nome de url, não.
#
# FORA, e por isso ausentes: `PUT /api/catalogo` e `PATCH /api/produtos/...`
# (substituídos por `integracao-catalogo-publicar`), sessão e senha (emitir
# credencial do Lobby é o que a asserção existe para não fazer), `/api/venda/*`
# (do consultor e do n8n), o `/api/historico` antigo, `/django-admin/`,
# `/metrics` e DELETE em qualquer rota.
ROTAS_LIBERADAS = {
    # Rotas novas, sob /api/integracao/.
    "integracao-quem-sou": frozenset({"GET"}),
    "integracao-saude": frozenset({"GET"}),
    "integracao-catalogo-publicar": frozenset({"POST"}),
    "integracao-categoria": frozenset({"PATCH"}),
    "integracao-historico": frozenset({"GET"}),
    # Rotas que já existiam.
    "catalogo": frozenset({"GET"}),
    "cobranca": frozenset({"GET", "PUT"}),
    "produtos": frozenset({"POST"}),
    "departamentos-configuracao": frozenset({"GET", "PUT"}),
    "categorias-financeiras-configuracao": frozenset({"GET", "PUT"}),
    "dh-avaliacao-valores-configuracao": frozenset({"GET", "PUT"}),
    "publicacoes": frozenset({"GET"}),
}

# O alias de cache do `jti` (ver o bloco CACHES em `config/settings.py`): falha
# FECHADA, ao contrário do `default` dos throttles.
ALIAS_DO_CACHE = "assercao"

# Os backends de cache em que um `add` vale para todos os processos. Lista de
# PERMITIDOS, não de proibidos: um backend novo, ou o `DummyCache` (cujo `add`
# devolve sempre verdadeiro e deixaria toda repetição passar), fica de fora até
# alguém decidir o contrário. O que falha aberto (`RedisQueNaoDerrubaAVenda`)
# também fica de fora: o `jti` não pode passar quando o Redis não responde.
CACHES_COMPARTILHADOS = frozenset(
    {
        "django.core.cache.backends.redis.RedisCache",
        "django.core.cache.backends.memcached.PyMemcacheCache",
        "django.core.cache.backends.memcached.PyLibMCCache",
        "django.core.cache.backends.db.DatabaseCache",
    }
)
CACHE_LOCAL = "django.core.cache.backends.locmem.LocMemCache"

# Cache que voltou VAZIO (Redis reiniciado, FLUSHDB) não sabe quais `jti` já
# foram usados — o Redis do Lobby não persiste, de propósito (TSK-1068). Uma
# asserção consumida antes do reinício ainda pode estar no prazo, e passaria de
# novo. Por isso o cache guarda, sem prazo, QUANDO começou a contar; até ele
# contar há mais tempo que a vida máxima de uma asserção, a integração fica
# fechada (503). A conta: aceita até `exp + tolerância`, com `exp` até
# VALIDADE_MAXIMA depois do `iat`, e o `iat` até `tolerância` no futuro — e a
# tolerância é a do teto (`CONECTA_ASSERCAO_TOLERANCIA_TETO`), não a
# configurada, para não depender dela. Fecha enquanto `agora - marco <= janela`.
MARCO_DO_CACHE = "assercao:jti:contando-desde"


def janela_depois_de_cache_vazio() -> int:
    return VALIDADE_MAXIMA + 2 * settings.CONECTA_ASSERCAO_TOLERANCIA_TETO


# ----- recusas ---------------------------------------------------------------
#
# Toda recusa carrega um `codigo`, e é por ele que o Conecta decide — nunca pela
# frase (§4.7). A frase não diz qual claim reprovou; o log diz.

FRASES = {
    "ASSERCAO_AUSENTE": "Esta rota exige a asserção do Conecta.",
    "ASSERCAO_MAL_FORMADA": "Asserção mal formada.",
    "CABECALHO_INVALIDO": "Cabeçalho da asserção inválido.",
    "KID_DESCONHECIDO": "Chave da asserção desconhecida.",
    "ASSINATURA_INVALIDA": "Assinatura da asserção inválida.",
    "ASSERCAO_EXPIRADA": "Asserção expirada.",
    "ASSERCAO_FUTURA": "Asserção ainda não vale.",
    "ASSERCAO_LONGA": "Validade da asserção acima do permitido.",
    "EMISSOR_ERRADO": "Emissor da asserção não reconhecido.",
    "AUDIENCIA_ERRADA": "Esta asserção não é para o Lobby.",
    "JTI_REPETIDO": "Asserção já usada.",
    "CLAIM_FALTANDO": "Asserção incompleta.",
    "IMPERSONACAO_RECUSADA": "Em \"Entrar como usuário\" o Lobby é só leitura.",
    "SEM_CONTA": "Sem conta no Lobby.",
    "CONTA_INATIVA": "Conta do Lobby inativa.",
    "ROTA_NAO_LIBERADA": "Esta rota não está liberada para o Conecta.",
    "SEM_PERMISSAO": "Sem permissão para isto no Lobby.",
    "MUITAS_CHAMADAS": "Muitas chamadas em pouco tempo.",
    "INTEGRACAO_DESLIGADA": "A integração com o Conecta não está ligada neste servidor.",
}

# Nível do log de cada recusa (§4.7). ERROR é tentativa ou configuração errada;
# WARNING é relógio; INFO é o caso legítimo de uma pessoa sem conta.
NIVEIS = {
    "ASSERCAO_AUSENTE": logging.INFO,
    "ASSERCAO_MAL_FORMADA": logging.WARNING,
    "CABECALHO_INVALIDO": logging.ERROR,
    "KID_DESCONHECIDO": logging.ERROR,
    "ASSINATURA_INVALIDA": logging.ERROR,
    "ASSERCAO_EXPIRADA": logging.WARNING,
    "ASSERCAO_FUTURA": logging.WARNING,
    "ASSERCAO_LONGA": logging.ERROR,
    "EMISSOR_ERRADO": logging.ERROR,
    "AUDIENCIA_ERRADA": logging.ERROR,
    "JTI_REPETIDO": logging.ERROR,
    "CLAIM_FALTANDO": logging.ERROR,
    "IMPERSONACAO_RECUSADA": logging.ERROR,
    "SEM_CONTA": logging.INFO,
    "CONTA_INATIVA": logging.INFO,
    "ROTA_NAO_LIBERADA": logging.ERROR,
    "SEM_PERMISSAO": logging.INFO,
    "MUITAS_CHAMADAS": logging.WARNING,
    "INTEGRACAO_DESLIGADA": logging.ERROR,
}


# Recusas que não contam no teto por IP: todas chegam com asserção de assinatura
# e claims BOAS (ver `AutenticacaoConecta.authenticate`). Sem conta e conta
# inativa são pessoas que ainda não podem; impersonação escrevendo e rota fora
# da lista são, pelo ADR-002 §4.7, contadas "por pessoa", não por IP — e todo o
# tráfego do Conecta sai de um IP só.
RECUSAS_LEGITIMAS = frozenset({"SEM_CONTA", "CONTA_INATIVA", "IMPERSONACAO_RECUSADA", "ROTA_NAO_LIBERADA"})


class AssercaoRecusada(exceptions.AuthenticationFailed):
    """401. Subclasse de `AuthenticationFailed` para o DRF manter o 401 e montar
    o `WWW-Authenticate` — sem isso ele o rebaixaria a 403."""

    def __init__(self, codigo):
        super().__init__(FRASES[codigo])
        self.codigo = codigo


class AssercaoProibida(exceptions.PermissionDenied):
    """403: asserção boa, mas o que ela pede não pode (rota fora da lista,
    escrita em impersonação)."""

    def __init__(self, codigo):
        super().__init__(FRASES[codigo])
        self.codigo = codigo


class IntegracaoDesligada(exceptions.APIException):
    status_code = 503
    default_detail = FRASES["INTEGRACAO_DESLIGADA"]

    def __init__(self):
        super().__init__()
        self.codigo = "INTEGRACAO_DESLIGADA"


class MuitasFalhas(exceptions.Throttled):
    def __init__(self, espera=None):
        super().__init__(wait=espera, detail=FRASES["MUITAS_CHAMADAS"])
        self.codigo = "MUITAS_CHAMADAS"


# ----- configuração ----------------------------------------------------------


class ChavesInvalidas(Exception):
    """`CONECTA_ASSERCAO_CHAVES` existe mas não é um conjunto JWK Ed25519 válido."""


def _b64url(texto: str) -> bytes:
    if not isinstance(texto, str) or not texto:
        raise ValueError("vazio")
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


_memoria_das_chaves = {"bruto": None, "chaves": None, "erro": None}


def chaves_publicas() -> dict:
    """As chaves públicas aceitas, por `kid`. `{}` quando não há configuração.

    Levanta `ChavesInvalidas` se a variável existe e está errada — e aí a
    integração fica desligada INTEIRA, com ERROR no log. Uma chave mal colada no
    meio de uma troca derruba o portal, alto; aceitar a metade boa esconderia o
    erro até a chave antiga ser retirada, que é o pior momento para descobrir.

    Lida uma vez por processo e guardada pelo texto cru: o `override_settings`
    dos testes troca o texto, e a memória se refaz sozinha.
    """
    bruto = getattr(settings, "CONECTA_ASSERCAO_CHAVES", "") or ""
    if _memoria_das_chaves["bruto"] == bruto:
        if _memoria_das_chaves["erro"]:
            raise ChavesInvalidas(_memoria_das_chaves["erro"])
        return _memoria_das_chaves["chaves"]

    chaves, erro = {}, None
    try:
        chaves = _le_chaves(bruto)
    except ChavesInvalidas as falha:
        erro = str(falha)
    _memoria_das_chaves.update(bruto=bruto, chaves=chaves, erro=erro)
    if erro:
        raise ChavesInvalidas(erro)
    return chaves


def _le_chaves(bruto: str) -> dict:
    if not bruto.strip():
        return {}
    try:
        conjunto = json.loads(bruto)
    except ValueError:
        raise ChavesInvalidas("não é JSON")
    lista = conjunto.get("keys") if isinstance(conjunto, dict) else None
    if not isinstance(lista, list) or not lista:
        raise ChavesInvalidas('esperado {"keys": [...]} com ao menos uma chave')

    chaves = {}
    for jwk in lista:
        if not isinstance(jwk, dict):
            raise ChavesInvalidas("chave que não é objeto")
        kid = jwk.get("kid")
        if not isinstance(kid, str) or not kid.strip():
            raise ChavesInvalidas("chave sem kid")
        if kid in chaves:
            # `kid` repetido com conteúdo diferente é a receita de "funciona num
            # servidor e falha no outro" (§8.1).
            raise ChavesInvalidas(f"kid repetido: {kid}")
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
            raise ChavesInvalidas(f"{kid}: só OKP/Ed25519")
        if jwk.get("alg", ALGORITMO) != ALGORITMO or jwk.get("use", "sig") != "sig":
            raise ChavesInvalidas(f"{kid}: alg/use diferentes de EdDSA/sig")
        if "d" in jwk:
            # A metade PRIVADA colada no lugar da pública. Funcionaria — e
            # deixaria no .env do Lobby a credencial que fala por qualquer pessoa.
            raise ChavesInvalidas(f"{kid}: isto é uma chave PRIVADA; use só a pública")
        try:
            crua = _b64url(jwk.get("x"))
            if len(crua) != 32:
                raise ValueError("tamanho")
            chaves[kid] = Ed25519PublicKey.from_public_bytes(crua)
        except (ValueError, TypeError):
            raise ChavesInvalidas(f"{kid}: x não é uma chave Ed25519 em base64url")
    return chaves


def _cache_do_jti():
    return caches[ALIAS_DO_CACHE] if ALIAS_DO_CACHE in settings.CACHES else caches["default"]


def cache_compartilhado() -> bool:
    configuracao = settings.CACHES.get(ALIAS_DO_CACHE) or settings.CACHES.get("default", {})
    backend = configuracao.get("BACKEND", "")
    if backend in CACHES_COMPARTILHADOS:
        return True
    # Em dev há um processo só, e o `LocMemCache` basta (§4.6).
    return bool(settings.DEBUG) and backend == CACHE_LOCAL


def motivo_de_desligada() -> str:
    """Por que a integração está desligada, ou "" se está ligada."""
    try:
        chaves = chaves_publicas()
    except ChavesInvalidas as erro:
        return f"CONECTA_ASSERCAO_CHAVES inválida ({erro})"
    if not chaves:
        return "CONECTA_ASSERCAO_CHAVES vazia"
    if not cache_compartilhado():
        return (
            "cache não compartilhado entre processos com DEBUG=False "
            "(configure LOBBY_REDIS_URL)"
        )
    return ""


def kids_aceitos() -> list[str]:
    try:
        return sorted(chaves_publicas())
    except ChavesInvalidas:
        return []


# ----- a asserção validada ---------------------------------------------------


@dataclass(frozen=True)
class AssercaoConecta:
    """O que sobra de uma asserção conferida. Vira o `request.auth`."""

    kid: str
    jti: str
    sub: str
    aud: str
    exp: int
    impersonacao: bool

    @property
    def so_leitura(self) -> bool:
        return self.impersonacao


def _registro(request) -> dict:
    """O que se sabe da asserção até aqui, para a linha de log da chamada.

    Mora no `HttpRequest` do Django, e não no `Request` do DRF, porque o handler
    de erro e o `finalize_response` só têm acesso fácil a esse.
    """
    django_request = getattr(request, "_request", request)
    registro = getattr(django_request, "assercao_registro", None)
    if registro is None:
        registro = {}
        django_request.assercao_registro = registro
    return registro


def _inteiro(valor) -> bool:
    # `True` é `int` em Python; um `"iat": true` não pode passar por data.
    return isinstance(valor, int) and not isinstance(valor, bool)


class FalhasPorIp(SimpleRateThrottle):
    """Conta as recusas de asserção por IP (§10.4).

    Não é um throttle de view: antes de a asserção ser aceita não há pessoa, e
    o DRF só roda os throttles depois da autenticação. A classe de autenticação
    pergunta `estourou()` antes de conferir e chama `conta()` a cada recusa.
    """

    scope = "integracao_falhas"

    def get_cache_key(self, request, view=None):
        return self.cache_format % {"scope": self.scope, "ident": ip_do_cliente(request)}

    def estourou(self, request) -> float | None:
        """Segundos de espera se o IP passou do teto; None se não passou."""
        self.key = self.get_cache_key(request)
        self.history = self.cache.get(self.key, [])
        self.now = self.timer()
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()
        if len(self.history) >= self.num_requests:
            return self.wait()
        return None

    def conta(self, request):
        self.key = self.get_cache_key(request)
        self.now = self.timer()
        historico = [t for t in self.cache.get(self.key, []) if t > self.now - self.duration]
        historico.insert(0, self.now)
        self.cache.set(self.key, historico, self.duration)


class AutenticacaoConecta(BaseAuthentication):
    """Autentica pela asserção quando o cabeçalho existe. Sem ele, não opina.

    É a classe das rotas que já existiam e passam a aceitar o Conecta ao lado do
    JWT de sessão do /admin: sem cabeçalho, devolve None e o DRF segue para a
    próxima classe — e o consultor anônimo e a diretoria no /admin não passam
    por nada daqui, nem pelo 503 da integração desligada.
    """

    obrigatoria = False

    def authenticate_header(self, request):
        return ESQUEMA

    def authenticate(self, request):
        token = request.META.get(CABECALHO)
        if token is None:
            if self.obrigatoria:
                if motivo_de_desligada():
                    raise IntegracaoDesligada()
                raise AssercaoRecusada("ASSERCAO_AUSENTE")
            return None

        # Cabeçalho presente, mesmo vazio, é chamada da integração: é conferido
        # como tal (vazio cai em ASSERCAO_MAL_FORMADA), nunca ignorado.
        registro = _registro(request)

        if motivo_de_desligada():
            raise IntegracaoDesligada()

        falhas = FalhasPorIp()
        try:
            espera = falhas.estourou(request)
        except Exception:
            logger.exception("cache indisponível ao conferir a asserção")
            raise IntegracaoDesligada()
        if espera is not None:
            raise MuitasFalhas(espera)

        try:
            return self._confere(request, token.strip(), registro)
        except (AssercaoRecusada, AssercaoProibida) as recusa:
            # Sem conta e conta inativa chegam com asserção de assinatura BOA: não
            # é alguém batendo na porta, é uma pessoa que ainda não entrou no
            # Lobby. Contá-las no teto por IP — e todo o tráfego do Conecta sai de
            # um IP só — deixaria a diretoria inteira em 429 porque meia dúzia
            # de pessoas sem conta abriu a tela.
            if recusa.codigo not in RECUSAS_LEGITIMAS:
                try:
                    falhas.conta(request)
                except Exception:
                    logger.exception("cache indisponível ao contar a recusa da asserção")
            raise

    # ----- a conferência, na ordem da §4.7 --------------------------------

    def _confere(self, request, token, registro):
        if len(token) > TAMANHO_MAXIMO or token.count(".") != 2:
            raise AssercaoRecusada("ASSERCAO_MAL_FORMADA")
        try:
            cabecalho = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AssercaoRecusada("ASSERCAO_MAL_FORMADA")

        # `alg` escrito à mão, nunca a lista padrão: é a defesa contra `none` e
        # contra HS256 usando a chave pública como segredo (§4.2).
        if cabecalho.get("alg") != ALGORITMO or cabecalho.get("typ") != "JWT":
            raise AssercaoRecusada("CABECALHO_INVALIDO")

        kid = cabecalho.get("kid")
        chave = chaves_publicas().get(kid) if isinstance(kid, str) else None
        if chave is None:
            # Sem tentar as outras chaves: `kid` desconhecido é recusa. O que veio
            # vai para o log CURTO e entre aspas: é texto de quem quer que tenha
            # mandado o cabeçalho, e cru ele poderia forjar uma linha de log.
            registro["kid"] = repr(kid)[:64]
            raise AssercaoRecusada("KID_DESCONHECIDO")
        registro["kid"] = kid

        try:
            claims = jwt.decode(
                token,
                key=chave,
                algorithms=[ALGORITMO],
                # As claims são conferidas abaixo, uma a uma, para cada recusa
                # ter o seu código e o log poder dizer a diferença medida.
                options={
                    "verify_signature": True,
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_sub": False,
                    "verify_jti": False,
                    "require": [],
                },
            )
        except jwt.InvalidSignatureError:
            raise AssercaoRecusada("ASSINATURA_INVALIDA")
        except jwt.PyJWTError:
            raise AssercaoRecusada("ASSERCAO_MAL_FORMADA")
        if not isinstance(claims, dict):
            raise AssercaoRecusada("ASSERCAO_MAL_FORMADA")

        registro["jti"] = claims.get("jti") if isinstance(claims.get("jti"), str) else None
        registro["sub"] = claims.get("sub") if isinstance(claims.get("sub"), str) else None
        registro["aud"] = claims.get("aud") if isinstance(claims.get("aud"), str) else repr(claims.get("aud"))

        assercao = self._confere_claims(claims, registro)

        # Escrita em impersonação: o Conecta não assina nessa sessão (§10.6), e
        # esta é a segunda trava, do outro lado do arame. Leitura passa: é o
        # modo "só leitura" que a tela do Conecta mostra.
        if assercao.impersonacao and request.method not in METODOS_DE_LEITURA:
            raise AssercaoProibida("IMPERSONACAO_RECUSADA")

        self._grava_jti(assercao)

        # Rota conferida depois da asserção estar provada boa, e antes de
        # qualquer leitura no banco: "asserção válida, rota fora da lista".
        resolvida = getattr(getattr(request, "_request", request), "resolver_match", None)
        nome_da_url = getattr(resolvida, "url_name", None)
        registro["url"] = nome_da_url
        if request.method not in ROTAS_LIBERADAS.get(nome_da_url, ()):
            raise AssercaoProibida("ROTA_NAO_LIBERADA")

        usuario = self._resolve_pessoa(assercao.sub)
        registro["ok"] = True
        return usuario, assercao

    def _confere_claims(self, claims, registro) -> AssercaoConecta:
        for nome in ("iss", "sub", "iat", "nbf", "exp", "jti", "imp"):
            if nome not in claims:
                registro["detalhe"] = f"sem {nome}"
                raise AssercaoRecusada("CLAIM_FALTANDO")
        tipos_ok = (
            isinstance(claims["sub"], str)
            and "@" in claims["sub"]
            and len(claims["sub"]) <= 254
            and isinstance(claims["jti"], str)
            and 0 < len(claims["jti"]) <= 100
            and isinstance(claims["imp"], bool)
            and all(_inteiro(claims[n]) for n in ("iat", "nbf", "exp"))
        )
        if not tipos_ok:
            registro["detalhe"] = "claim com tipo errado"
            raise AssercaoRecusada("CLAIM_FALTANDO")

        if claims["iss"] != settings.CONECTA_ASSERCAO_EMISSOR:
            raise AssercaoRecusada("EMISSOR_ERRADO")
        # Texto, e igual. Lista é recusada mesmo contendo "lobby": é o caminho
        # natural para uma asserção "para todos" (§4.3).
        if not isinstance(claims.get("aud"), str) or claims["aud"] != AUDIENCIA:
            raise AssercaoRecusada("AUDIENCIA_ERRADA")

        iat, nbf, exp = claims["iat"], claims["nbf"], claims["exp"]
        if exp - iat > VALIDADE_MAXIMA:
            registro["detalhe"] = f"exp-iat={exp - iat}s"
            raise AssercaoRecusada("ASSERCAO_LONGA")

        tolerancia = settings.CONECTA_ASSERCAO_TOLERANCIA_SEGUNDOS
        agora = int(time.time())
        if agora > exp + tolerancia:
            registro["detalhe"] = f"venceu há {agora - exp}s"
            raise AssercaoRecusada("ASSERCAO_EXPIRADA")
        adiantada = max(nbf, iat) - agora
        if adiantada > tolerancia:
            registro["detalhe"] = f"{adiantada}s no futuro"
            raise AssercaoRecusada("ASSERCAO_FUTURA")

        act = claims.get("act")
        return AssercaoConecta(
            kid=registro["kid"],
            jti=claims["jti"],
            sub=claims["sub"].strip().lower(),
            aud=claims["aud"],
            exp=exp,
            # `act` preenchido conta como impersonação mesmo com `imp` falso:
            # as duas claims são a mesma trava, e qualquer uma basta.
            impersonacao=bool(claims["imp"]) or act is not None,
        )

    def _grava_jti(self, assercao: AssercaoConecta):
        """`SET NX EX` no Redis: duas chamadas com o mesmo `jti` não passam
        as duas (§4.6). Guardado só até o `exp` mais a tolerância."""
        tolerancia = settings.CONECTA_ASSERCAO_TOLERANCIA_SEGUNDOS
        agora = int(time.time())
        # +1: a asserção é aceita enquanto o SEGUNDO inteiro de agora não passa
        # de `exp + tolerância`, ou seja, até o fim desse segundo; o prazo conta
        # do instante fracionário de agora e sumiria antes.
        prazo = max(1, assercao.exp - agora + tolerancia + 1)
        janela = janela_depois_de_cache_vazio()
        cache = _cache_do_jti()
        chave = f"assercao:jti:{AUDIENCIA}:{assercao.jti}"
        try:
            gravou = cache.add(chave, 1, timeout=prazo)
            # Ver MARCO_DO_CACHE. `add` sem prazo: só o primeiro depois do
            # cache vazio grava; os outros leem o que ele gravou.
            #
            # A ORDEM IMPORTA: o marco é lido DEPOIS de gravar o `jti`. Um
            # esvaziamento que deixe o `jti` repetido ser gravado de novo levou
            # o marco junto, e o marco lido aqui é posterior a ele — cai na
            # janela. Lido antes, um FLUSHDB entre a leitura e a gravação
            # deixaria a repetição passar com o marco velho.
            cache.add(MARCO_DO_CACHE, agora, timeout=None)
            desde = cache.get(MARCO_DO_CACHE)
            na_janela = not isinstance(desde, int) or agora - desde <= janela
            if gravou and na_janela:
                # A asserção leva 503 sem ter sido atendida: não fica queimada
                # para o Conecta tentar de novo depois da janela.
                cache.delete(chave)
        except Exception:
            # Cache fora: sem como saber se é repetição. Falha fechada.
            logger.exception("cache indisponível ao gravar o jti da asserção")
            raise IntegracaoDesligada()
        if not gravou:
            raise AssercaoRecusada("JTI_REPETIDO")
        if na_janela:
            logger.warning(
                "cache do jti recém-esvaziado (Redis reiniciado?); a integração "
                "fica fechada até %ss depois do primeiro uso",
                janela,
            )
            raise IntegracaoDesligada()

    def _resolve_pessoa(self, email):
        """`User.email__iexact = sub`. NUNCA cria conta (§13.2).

        E-mail repetido entre duas contas é recusado, e não resolvido pela de
        menor id (§12.4): escolher uma seria entregar a uma pessoa o papel da
        outra.
        """
        Usuario = get_user_model()
        contas = list(
            Usuario.objects.filter(email__iexact=email).select_related("vinculo_identidade")[:2]
        )
        if not contas:
            raise AssercaoRecusada("SEM_CONTA")
        if len(contas) > 1:
            logger.error("asserção para %s recusada: há mais de uma conta com este e-mail", email)
            raise AssercaoRecusada("SEM_CONTA")
        usuario = contas[0]
        if not usuario.is_active:
            raise AssercaoRecusada("CONTA_INATIVA")
        return usuario


class AutenticacaoConectaObrigatoria(AutenticacaoConecta):
    """A das rotas novas, sob /api/integracao/: só a asserção entra.

    Sem cabeçalho é 401 `ASSERCAO_AUSENTE` — ou 503 se a integração está
    desligada, que é o que essas rotas respondem nesse caso, com ou sem
    cabeçalho.
    """

    obrigatoria = True


# ----- throttles por pessoa --------------------------------------------------


class _ThrottleDaIntegracao(SimpleRateThrottle):
    """Teto por PESSOA, só para quem chegou pela asserção (§10.4).

    Quem vem pelo /admin de hoje não é contado: as rotas de escrita dele nunca
    tiveram teto, de propósito ("um teto ali atrapalharia a diretoria corrigindo
    um preço errado às pressas").
    """

    leitura = True

    def get_cache_key(self, request, view):
        if not isinstance(getattr(request, "auth", None), AssercaoConecta):
            return None
        if (request.method in METODOS_DE_LEITURA) != self.leitura:
            return None
        return self.cache_format % {"scope": self.scope, "ident": request.user.pk}


class IntegracaoLeituraThrottle(_ThrottleDaIntegracao):
    scope = "integracao_leitura"
    leitura = True


class IntegracaoEscritaThrottle(_ThrottleDaIntegracao):
    scope = "integracao_escrita"
    leitura = False


# ----- o mixin das views -----------------------------------------------------


class AceitaAssercao:
    """Mixin de view: declara, rota a rota, que o Conecta pode chamá-la.

    Põe a `AutenticacaoConecta` ANTES do JWT de sessão: com o cabeçalho
    presente, a chamada é da integração e é conferida como tal — uma asserção
    ruim não cai, quieta, no JWT. Sem o cabeçalho, nada muda para o /admin.

    `assercao_obrigatoria = True` nas rotas novas: aí só a asserção entra.

    A linha de log de cada chamada da integração sai daqui, no
    `finalize_response`, que roda também quando a autenticação recusou.
    """

    assercao_obrigatoria = False

    def dispatch(self, request, *args, **kwargs):
        if CABECALHO in request.META or self.assercao_obrigatoria:
            _registro(request)["inicio"] = time.monotonic()
        return super().dispatch(request, *args, **kwargs)

    def get_authenticators(self):
        if self.assercao_obrigatoria:
            return [AutenticacaoConectaObrigatoria()]
        return [AutenticacaoConecta(), *super().get_authenticators()]

    def get_authenticate_header(self, request):
        # O DRF monta o `WWW-Authenticate` do 401 pelo PRIMEIRO autenticador, que
        # aqui é o da asserção. Para quem não mandou a asserção (o /admin de
        # hoje), o 401 continua dizendo o que dizia: `Bearer realm="api"`.
        if not self.assercao_obrigatoria and CABECALHO not in request.META:
            for autenticador in self.get_authenticators()[1:]:
                cabecalho = autenticador.authenticate_header(request)
                if cabecalho:
                    return cabecalho
        return super().get_authenticate_header(request)

    def check_throttles(self, request):
        # Os da view primeiro (alguns sobrescrevem `get_throttles`, e por isso
        # os da integração não entram por lá).
        super().check_throttles(request)
        for throttle in (IntegracaoLeituraThrottle(), IntegracaoEscritaThrottle()):
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if CABECALHO in request.META or self.assercao_obrigatoria:
            registra_chamada(request, response)
        return response


def registra_chamada(request, response):
    """Uma linha por chamada da integração (§10.3). Nunca a asserção."""
    registro = _registro(request)
    codigo = None
    dados = getattr(response, "data", None)
    if isinstance(dados, dict):
        codigo = dados.get("codigo")
    inicio = registro.get("inicio")
    duracao = f"{(time.monotonic() - inicio) * 1000:.0f}ms" if inicio else "-"
    nivel = NIVEIS.get(codigo, logging.INFO) if codigo else logging.INFO
    resolvida = getattr(getattr(request, "_request", request), "resolver_match", None)
    logger.log(
        nivel,
        "integracao kid=%s jti=%s sub=%s aud=%s url=%s metodo=%s status=%s codigo=%s "
        "tempo=%s ip=%s%s",
        registro.get("kid", "-"),
        registro.get("jti", "-"),
        registro.get("sub", "-"),
        registro.get("aud", "-"),
        getattr(resolvida, "url_name", None) or "-",
        request.method,
        response.status_code,
        codigo or "-",
        duracao,
        ip_do_cliente(request),
        f" detalhe={registro['detalhe']}" if registro.get("detalhe") else "",
    )


# ----- o resto do Lobby ------------------------------------------------------


class FechaRotasForaDaLista:
    """Middleware: o cabeçalho da asserção numa view que não a declara é 403.

    A classe de autenticação só existe nas views com `AceitaAssercao`; nas
    outras o cabeçalho seria simplesmente ignorado — e o Conecta veria um 401
    genérico, ou pior, um 200 anônimo numa rota pública, sem `codigo` para
    decidir. Aqui a lista fechada vale para o Lobby inteiro, inclusive para o
    /django-admin/ e o /metrics: qualquer view fora dela responde
    `ROTA_NAO_LIBERADA`, antes de rodar (§5, "qualquer outra rota responde 403").
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if CABECALHO not in request.META:
            return None
        classe = getattr(view_func, "view_class", None) or getattr(view_func, "cls", None)
        if isinstance(classe, type) and issubclass(classe, AceitaAssercao):
            return None

        from django.http import JsonResponse

        frase = FRASES["ROTA_NAO_LIBERADA"]
        resposta = JsonResponse(
            {"erro": frase, "detail": frase, "codigo": "ROTA_NAO_LIBERADA"}, status=403
        )
        resposta["Cache-Control"] = "no-store"
        resolvida = getattr(request, "resolver_match", None)
        logger.error(
            "integracao kid=- jti=- sub=- aud=- url=%s metodo=%s status=403 "
            "codigo=ROTA_NAO_LIBERADA tempo=- ip=%s",
            getattr(resolvida, "url_name", None) or request.path,
            request.method,
            ip_do_cliente(request),
        )
        return resposta


def recusa(request, frase, status=400, codigo="DADOS_INVALIDOS"):
    """O 400 das views da lista, com `codigo` para o Conecta.

    Para o /admin de hoje a resposta continua `{"erro": ...}`, byte a byte: o
    `codigo` (e o `detail` do ADR) só entram quando a chamada veio com a
    asserção.
    """
    from rest_framework.response import Response

    corpo = {"erro": frase}
    if CABECALHO in request.META:
        corpo.update(detail=frase, codigo=codigo)
    return Response(corpo, status=status)
