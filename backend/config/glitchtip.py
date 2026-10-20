"""
Erros do backend no GlitchTip da SEJA AP (TSK-1142), sem dado pessoal.

O GlitchTip (https://erros.sejaap.com.br, projeto `lobby`) fala o protocolo do
Sentry, então o cliente é o `sentry-sdk`. Quem liga é o `config/settings.py`,
por `ligar_pelo_ambiente`: só com `SENTRY_DSN` no ambiente e nunca na suíte.
Sem DSN o SDK nem é iniciado e nada sai da máquina.

É o desenho do Conecta (TSK-1136, `utils/glitchtip.py` de lá), adaptado ao
Lobby: as máscaras de caminho do Conecta (link de senha e de pesquisa) não
existem aqui, e entram as credenciais deste app — o `AppKey` do Conecta ID, a
URL do banco e do Redis com senha, a chave do Omie, a asserção e o comprovante.

LGPD: o Lobby trata CNPJ, CPF, e-mail e o cronograma de parcelas do cliente, e o
evento não pode carregar nada disso. Por isso a configuração abaixo é fechada
por padrão e o `limpar_evento` passa por cima de tudo:

- `send_default_pii=False`: sem usuário, IP e cookies coletados pelo SDK;
- `include_local_variables=False`: as variáveis de cada frame (o `request.data`
  da venda, o payload do comprovante) não saem; fica só o código e a linha;
- `max_request_body_size='never'`: o corpo da requisição não é lido;
- log não vira evento nem rastro (`LoggingIntegration(level=None,
  event_level=None)`): a linha de log da integração leva IP e e-mail, e regex não
  acha nome de cliente. O que vira evento é a exceção NÃO tratada (500 do Django
  e do DRF, comando de manage.py que morre);
- sem tracing, sem profiling e sem sessões de release health: a VPS de soluções
  internas tem pouca folga;
- sem as integrações automáticas (`auto_enabling_integrations=False`): a do Redis
  gravaria no rastro a chave de cada comando, e a chave dos tetos de chamada
  leva o IP de quem vende.

`limpar_evento` é o `before_send`: tira cookies, corpo, query string, ambiente
WSGI e usuário da requisição, deixa só cabeçalhos de uma lista fechada (o
Authorization, o Cookie e o X-Conecta-Assercao não saem, nem mascarados), apaga
variáveis de frame e mascara, em qualquer texto do evento, CNPJ (inclusive o
alfanumérico), CPF, e-mail, telefone, JWT, `Bearer`, `AppKey`, usuário e senha
dentro de URL e o `DETAIL` dos erros do Postgres, além do valor de toda chave com
nome sensível (senha, token, authorization, cookie, cpf, cnpj, e-mail...),
inclusive dentro de JSON, repr ou formulário em texto.

As regras do front estão em `glitchtip/lobby.js`: mudou uma, mude a outra.
"""

import os
import re
import sys

FILTRADO = "[Filtrado]"

# Chave cujo VALOR nunca sai, em qualquer nível do evento. As do Conecta, mais as
# do Lobby: a chave de app (o `app_key` do Omie e o `AppKey` do Conecta ID), a
# asserção do Conecta, o comprovante de venda e o seu nonce, e os campos de
# cadastro do cliente que regex nenhuma reconhece pelo valor.
CHAVE_SENSIVEL = re.compile(
    r"senha|password|passwd|pwd|secret|segredo|token|authori[sz]ation|auth|cookie|"
    r"session|sessao|sessão|csrf|chave|api[_-]?key|app[_-]?key|jwt|dsn|signature|"
    r"assinatura|assercao|asserção|comprovante|nonce|"
    r"cpf|cnpj|e-?mail|telefone|celular|phone|"
    r"razao_social|representante|endereco|endereço|logradouro",
    re.IGNORECASE,
)

# Cabeçalhos que ajudam a entender o erro e não identificam ninguém. Lista
# fechada: o resto (Authorization, Cookie, X-Conecta-Assercao, X-Forwarded-For,
# CF-Connecting-IP, X-Real-IP...) não sai, nem mascarado.
CABECALHOS_PERMITIDOS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "content-length",
    "content-type",
    "host",
    "origin",
    "referer",
    "user-agent",
    "x-requested-with",
}

_NOMES_SENSIVEIS = CHAVE_SENSIVEL.pattern

# Só para texto que não é código: nas linhas de código dos frames, `senha = ...`
# e `.filter(cnpj=cnpj_limpo)` são nomes de variável, e a máscara os estragaria.
_MASCARAS_DE_CHAVE = (
    # Chave sensível dentro de um TEXTO: JSON ou repr de dict ("senha": "..." e
    # 'senha': '...'), formulário, query e argumento nomeado (senha=..., senha='...').
    # Os trechos de nome têm teto ({0,64}, {0,40}) para a regex não ficar quadrática.
    # O fim do texto vale como aspa de fechamento: num texto cortado, o valor que
    # ficou aberto ("senha": "Minha senha#20...) sai inteiro como [Filtrado], mesmo
    # que o corte tenha caído logo depois da barra de um escape (ç, \n).
    (re.compile(r'("[^"\n]{0,64}?(?:' + _NOMES_SENSIVEIS + r')[^"\n]{0,64}?"\s*:\s*)"(?:[^"\\]|\\.)*(?:"|\\?\Z)',
                re.IGNORECASE), r'\1"[Filtrado]"'),
    (re.compile(r"('[^'\n]{0,64}?(?:" + _NOMES_SENSIVEIS + r")[^'\n]{0,64}?'\s*:\s*)'(?:[^'\\]|\\.)*(?:'|\\?\Z)",
                re.IGNORECASE), r"\1'[Filtrado]'"),
    (re.compile(r"(\b\w{0,40}?(?:" + _NOMES_SENSIVEIS + r")\w{0,40}?=)"
                r"(?:'[^'\n]*(?:'|\Z)|\"[^\"\n]*(?:\"|\Z)|[^&\s\"'()\[\],;]+)",
                re.IGNORECASE), r"\1[Filtrado]"),
)

# Ordem importa: o JWT, o Bearer e a credencial da URL antes do e-mail e dos
# números; o CNPJ (14) antes do CPF (11).
_MASCARAS_DE_DADO = (
    # Erro do Postgres traz a linha recusada inteira (razão social, endereço,
    # telefone do cliente), e regex não acha nome: do `DETAIL:` em diante nada sai
    # (nem a coluna do `Key (...)`, nem o HINT; a constraint fica na primeira
    # linha). Fora do DETAIL, o valor do `Key (coluna)=(valor)` some e a coluna fica.
    (re.compile(r"DETAIL:.*", re.DOTALL), "DETAIL: [Filtrado]"),
    (re.compile(r"Failing row contains \(.*", re.DOTALL), "Failing row contains ([Filtrado])"),
    (re.compile(r"(\bKey \([^)]*\))=\((?:[^()]|\([^()]*(?:\)|\Z))*(?:\)|\Z)"), r"\1=([Filtrado])"),
    (re.compile(r"(?i)\bbearer\s+[^\s,;\"']+"), "Bearer [token]"),
    # A chave do Lobby no Conecta ID vai como `Authorization: AppKey lobby:<segredo>`.
    (re.compile(r"\bAppKey\s+[^\s,;\"']+"), "AppKey [token]"),
    # Sem exigir os dois pontos: um JWT cortado ao meio continua reconhecível.
    (re.compile(r"\beyJ[\w-]+(?:\.[\w-]*)*"), "[token]"),
    # Usuário e senha dentro de URL: o DATABASE_URL, o LOBBY_REDIS_URL e o próprio
    # DSN. Antes do e-mail, que comeria só o pedaço depois dos dois pontos.
    (re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]{0,20}://)[^\s/@\"'<>]{1,256}@"), r"\1[credencial]@"),
    # Partes com teto (64 e 253, os do RFC): sem ele a regex reescaneia cada trecho
    # longo sem `@` e fica quadrática (50 KB custavam segundos).
    (re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}"), "[e-mail]"),
    # CNPJ com máscara, numérico ou alfanumérico (o novo, de 2026: 12 posições
    # de letra ou número e 2 dígitos verificadores).
    (re.compile(r"\b[A-Z0-9]{2}\.[A-Z0-9]{3}\.[A-Z0-9]{3}/[A-Z0-9]{4}-\d{2}\b"), "[CNPJ]"),
    (re.compile(r"\b[A-Z0-9]{12}\d{2}\b"), "[CNPJ]"),
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[CPF]"),
    (re.compile(r"\b\d{11}\b"), "[CPF]"),
    (re.compile(r"\(\d{2}\)\s?\d{4,5}-?\d{4}\b"), "[telefone]"),
    (re.compile(r"\+55\s?\(?\d{2}\)?\s?\d{4,5}-?\d{4}\b"), "[telefone]"),
    (re.compile(r"\b[1-9]\d\s(?:9\d{4}|[2-5]\d{3})-\d{4}\b"), "[telefone]"),
)

# Profundidade máxima da varredura: o SDK já corta o evento bem antes disso.
_PROFUNDIDADE = 20

# Teto de cada texto: o before_send roda no worker que está devolvendo o 500. As
# máscaras rodam sobre um pouco mais que isso (a folga), e só depois vem o corte final.
_TAMANHO_MAXIMO = 16384
_FOLGA_DAS_MASCARAS = 1024

# `max_value_length` do SDK: ele corta cada texto em até tantos bytes, com '...' no
# fim, ANTES do before_send.
MAX_VALUE_LENGTH = 8192
_MARCA_DO_SDK = "..."

# Linhas do NOSSO código em volta do erro: passam pelas máscaras de dado (há CNPJ
# e e-mail literais no código), mas não pelas de chave em texto.
_CONTEXTO_DE_CODIGO = {"pre_context", "context_line", "post_context"}

# Caracteres de que CPF, CNPJ, e-mail, JWT e URL são feitos.
_PONTUACAO_DE_DADO = frozenset("._%+-@/=:")


def _sem_resto_cortado(texto):
    """
    Tira do fim a sequência de caracteres de dado que o corte pode ter partido: um
    "123.456.7" ou um "fulana@empre" já não casam com a máscara e sairiam crus.
    """
    fim = len(texto)
    while fim and (texto[fim - 1].isalnum() or texto[fim - 1] in _PONTUACAO_DE_DADO):
        fim -= 1
    return texto[:fim].rstrip()


def _cortado_pelo_sdk(texto):
    return (texto.endswith(_MARCA_DO_SDK) and len(texto) * 4 >= MAX_VALUE_LENGTH - 8
            and len(texto.encode("utf-8", "ignore")) >= MAX_VALUE_LENGTH - 8)


def mascarar_texto(texto, codigo=False):
    """
    Troca CNPJ, CPF, e-mail, telefone e token dentro de um texto qualquer.

    O corte vem DEPOIS das máscaras, e o que um corte partir ao meio sai inteiro:
    pedaço de dado no fim de um texto cortado não casa com regex nenhuma.
    `codigo=True` (linha de código de um frame) pula as máscaras de chave em texto.
    """
    cortado = False
    if _cortado_pelo_sdk(texto):
        texto, cortado = _sem_resto_cortado(texto[:-len(_MARCA_DO_SDK)]), True
    limite = _TAMANHO_MAXIMO + _FOLGA_DAS_MASCARAS
    if len(texto) > limite:
        texto, cortado = _sem_resto_cortado(texto[:limite]), True
    grupos = (_MASCARAS_DE_DADO,) if codigo else (_MASCARAS_DE_CHAVE, _MASCARAS_DE_DADO)
    for grupo in grupos:
        for padrao, troca in grupo:
            texto = padrao.sub(troca, texto)
    if len(texto) > _TAMANHO_MAXIMO:
        texto, cortado = _sem_resto_cortado(texto[:_TAMANHO_MAXIMO]), True
    return texto + " [cortado]" if cortado else texto


def _sem_query(url):
    """URL sem query string nem fragmento: o link de definir senha leva o token na query."""
    if not isinstance(url, str):
        return url
    return url.split("?", 1)[0].split("#", 1)[0]


def _varrer(valor, profundidade=0, codigo=False):
    if profundidade > _PROFUNDIDADE:
        return FILTRADO
    if isinstance(valor, str):
        return mascarar_texto(valor, codigo=codigo)
    if isinstance(valor, dict):
        limpo = {}
        for chave, item in valor.items():
            if isinstance(chave, str) and CHAVE_SENSIVEL.search(chave):
                limpo[chave] = FILTRADO
            else:
                limpo[chave] = _varrer(item, profundidade + 1,
                                       codigo=codigo or chave in _CONTEXTO_DE_CODIGO)
        return limpo
    if isinstance(valor, (list, tuple)):
        return [_varrer(item, profundidade + 1, codigo=codigo) for item in valor]
    return valor


def _limpar_requisicao(requisicao):
    if not isinstance(requisicao, dict):
        return None
    cabecalhos = requisicao.get("headers") or {}
    limpa = {
        "method": requisicao.get("method"),
        "url": _sem_query(requisicao.get("url")),
        "headers": {
            nome: _sem_query(valor) if nome.lower() in ("referer", "origin") else valor
            for nome, valor in cabecalhos.items()
            if isinstance(nome, str) and nome.lower() in CABECALHOS_PERMITIDOS
        },
    }
    return {chave: valor for chave, valor in limpa.items() if valor}


def _sem_variaveis_de_frame(evento):
    blocos = [evento]
    blocos.extend((evento.get("exception") or {}).get("values") or [])
    blocos.extend((evento.get("threads") or {}).get("values") or [])
    for bloco in blocos:
        for frame in ((bloco.get("stacktrace") or {}).get("frames") or []):
            frame.pop("vars", None)


def _limpar_rastros(evento):
    rastros = evento.get("breadcrumbs")
    lista = rastros.get("values") if isinstance(rastros, dict) else rastros
    for rastro in lista or []:
        dados = rastro.get("data")
        if isinstance(dados, dict):
            for campo in ("url", "to", "from"):
                if campo in dados:
                    dados[campo] = _sem_query(dados[campo])
            dados.pop("http.query", None)
            dados.pop("http.fragment", None)


def limpar_evento(evento, hint=None):
    """`before_send` do SDK: o evento sai sem dado pessoal (ver o docstring do módulo)."""
    if not isinstance(evento, dict):
        return evento
    evento.pop("user", None)
    # As anotações do SDK sobre a requisição falam de campos que já não existem aqui.
    if isinstance(evento.get("_meta"), dict):
        evento["_meta"].pop("request", None)
        evento["_meta"].pop("user", None)
    if "request" in evento:
        requisicao = _limpar_requisicao(evento["request"])
        if requisicao:
            evento["request"] = requisicao
        else:
            evento.pop("request")
    _sem_variaveis_de_frame(evento)
    _limpar_rastros(evento)
    return _varrer(evento)


def iniciar(dsn, release=None, environment="production", transport=None):
    """
    Liga o SDK. Sem DSN não faz nada e devolve False.

    `transport` existe para o teste capturar o evento sem rede; em produção fica
    o padrão do SDK, que manda para o GlitchTip do DSN.
    """
    dsn = (dsn or "").strip()
    if not dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    opcoes = {}
    if transport is not None:
        opcoes["transport"] = transport

    try:
        sentry_sdk.init(
            dsn=dsn,
            release=(release or "").strip() or None,
            environment=(environment or "").strip() or "production",
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            # Trunca cada texto do evento antes do before_send.
            max_value_length=MAX_VALUE_LENGTH,
            before_send=limpar_evento,
            # Sem tracing (`traces_sample_rate` ausente = desligado) e sem profiling.
            auto_session_tracking=False,
            send_client_reports=False,
            # Sem isto o SDK põe `sentry-trace` e `baggage` em TODA chamada de saída
            # (Conecta ID, Omie), mesmo com o tracing desligado.
            trace_propagation_targets=[],
            # Só as integrações listadas abaixo e as básicas do SDK. As automáticas
            # ligam sozinhas quando o pacote está instalado, e a do Redis grava a
            # chave de cada comando no rastro — a dos tetos de chamada leva o IP.
            auto_enabling_integrations=False,
            integrations=[
                DjangoIntegration(transaction_style="url"),
                LoggingIntegration(level=None, event_level=None),
            ],
            **opcoes,
        )
    except Exception as erro:  # noqa: BLE001
        # DSN malformado (sem a chave, sem esquema) não pode derrubar o Django: o
        # import de settings morreria, e com ele o backend e os dois laços do Omie.
        # Sem GlitchTip, o resto segue.
        print(f"[GLITCHTIP] desligado: SENTRY_DSN inválido ({type(erro).__name__})", flush=True)
        return False
    print(f"[GLITCHTIP] ligado (release={release or '-'})", flush=True)
    return True


def rodando_testes(argv=None, modulos=None, ambiente=None):
    """`manage.py test` (o critério do SECURE_SSL_REDIRECT do settings) ou pytest."""
    argv = sys.argv if argv is None else argv
    modulos = sys.modules if modulos is None else modulos
    ambiente = os.environ if ambiente is None else ambiente
    if ambiente.get("PYTEST_CURRENT_TEST") or "pytest" in modulos:
        return True
    argv0 = os.path.basename(argv[0] if argv else "")
    return argv0.startswith("pytest") or argv0 == "py.test" or "test" in argv[1:2]


def ligar_pelo_ambiente(ambiente=None, argv=None, modulos=None):
    """
    O que o settings chama. Na suíte o SDK nunca liga, nem com SENTRY_DSN no
    ambiente ou no .env; fora dela, liga só com o DSN.

    SENTRY_DSN          DSN do projeto `lobby` em https://erros.sejaap.com.br.
                        Vazio = SDK desligado, nada é enviado.
    SENTRY_ENVIRONMENT  'production' se ausente.
    GIT_COMMIT          vira o `release`. Vem gravado na imagem pelo build (ver o
                        backend/Dockerfile e o docker-compose.yml): é o commit
                        da imagem que está no ar.
    """
    ambiente = os.environ if ambiente is None else ambiente
    if rodando_testes(argv=argv, modulos=modulos, ambiente=ambiente):
        return False
    return iniciar(
        ambiente.get("SENTRY_DSN", ""),
        release=ambiente.get("GIT_COMMIT", "").strip() or None,
        environment=ambiente.get("SENTRY_ENVIRONMENT", "").strip() or "production",
    )
