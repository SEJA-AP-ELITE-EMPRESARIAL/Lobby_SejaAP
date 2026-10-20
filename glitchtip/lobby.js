/*
 * Erros do front do Lobby no GlitchTip da SEJA AP (TSK-1142), sem dado pessoal.
 *
 * O GlitchTip (https://erros.sejaap.com.br, projeto `lobby`) fala o protocolo do
 * Sentry. O Lobby não tem build (React e Babel vêm de CDN e o app é transpilado
 * no navegador), então o SDK entra como arquivo, com versão fixa:
 * `sentry-browser-11.8.0.min.js`, ao lado deste, cópia byte a byte do
 * https://browser.sentry-cdn.com/11.8.0/bundle.min.js (o sha256 é conferido em
 * tests/front/glitchtip.test.mjs). A mesma versão que o Conecta usa (TSK-1136).
 * Sem build não há source map a enviar: a pilha aponta para o index.html.
 *
 * Quem liga é a própria página: os três HTMLs carregam, no <head> e antes do
 * React, o `/glitchtip/config.js`, o SDK e este arquivo. O `config.js` é escrito
 * pelo container do front na subida (deploy/frontend/glitchtip-config.sh), com o
 * SENTRY_DSN do .env e o GIT_COMMIT gravado na imagem. Sem DSN (dev, `npx serve`,
 * testes), o SDK nem é iniciado e nada sai do navegador.
 *
 * LGPD: o consultor digita CNPJ, CPF, e-mail e o cronograma de parcelas do
 * cliente, e o evento não pode carregar nada disso.
 * - `sendDefaultPii: false`, o `dataCollection` todo fechado e nenhum `setUser`;
 * - integrações escolhidas a dedo: sem rastro de console (log leva dado do
 *   cliente) nem de clique (o seletor leva texto da tela), sem sessões, sem
 *   tracing e sem replay;
 * - `limparEvento` (o `beforeSend`) e `limparRastro` (o `beforeBreadcrumb`) tiram
 *   a query string das URLs (o link de definir senha leva o token nela), deixam
 *   só cabeçalhos de uma lista fechada e mascaram CNPJ (inclusive o
 *   alfanumérico), CPF, e-mail, telefone, JWT, Bearer, usuário e senha dentro de
 *   URL e o `DETAIL` do Postgres em qualquer texto, além do valor de toda chave
 *   com nome sensível, também dentro de JSON ou formulário em texto;
 * - promessa rejeitada com objeto só leva nome, mensagem, código e status.
 *
 * O IP de quem usa NÃO se resolve aqui: o GlitchTip grava o IP da conexão por
 * cima do que o evento trouxer, e quem o tira é o `scrub_config` do projeto, no
 * servidor (TSK-1136).
 *
 * As regras espelham backend/config/glitchtip.py: mudou uma, mude a outra.
 */
(function (raiz) {
  "use strict";

  var FILTRADO = "[Filtrado]";

  var CHAVE_SENSIVEL = new RegExp(
    "senha|password|passwd|pwd|secret|segredo|token|authori[sz]ation|auth|cookie|" +
      "session|sessao|sessão|csrf|chave|api[_-]?key|app[_-]?key|jwt|dsn|signature|" +
      "assinatura|assercao|asserção|comprovante|nonce|" +
      "cpf|cnpj|e-?mail|telefone|celular|phone|" +
      "razao_social|representante|endereco|endereço|logradouro",
    "i",
  );

  var CABECALHOS_PERMITIDOS = new Set(["accept", "accept-language", "content-type", "referer", "user-agent"]);

  var NOMES_SENSIVEIS = CHAVE_SENSIVEL.source;

  // Só para texto que não é código: nas linhas de código dos frames, `senha = ...`
  // é nome de variável, e a máscara o estragaria.
  var MASCARAS_DE_CHAVE = [
    // Chave sensível dentro de um TEXTO: JSON ou objeto com aspas simples
    // ("senha": "..." e 'senha': '...'), formulário, query e argumento nomeado.
    // Os trechos de nome têm teto ({0,64}, {0,40}) para a regex não ficar quadrática.
    // O fim do texto vale como aspa de fechamento: num texto cortado, o valor que
    // ficou aberto sai inteiro como [Filtrado], mesmo com o corte logo depois da
    // barra de um escape.
    [
      new RegExp('("[^"\\n]{0,64}?(?:' + NOMES_SENSIVEIS + ')[^"\\n]{0,64}?"\\s*:\\s*)"(?:[^"\\\\]|\\\\.)*(?:"|\\\\?$)', "gi"),
      '$1"[Filtrado]"',
    ],
    [
      new RegExp("('[^'\\n]{0,64}?(?:" + NOMES_SENSIVEIS + ")[^'\\n]{0,64}?'\\s*:\\s*)'(?:[^'\\\\]|\\\\.)*(?:'|\\\\?$)", "gi"),
      "$1'[Filtrado]'",
    ],
    [
      new RegExp(
        "(\\b\\w{0,40}?(?:" + NOMES_SENSIVEIS + ")\\w{0,40}?=)(?:'[^'\\n]*(?:'|$)|\"[^\"\\n]*(?:\"|$)|[^&\\s\"'()\\[\\],;]+)",
        "gi",
      ),
      "$1[Filtrado]",
    ],
  ];

  // Ordem importa: o JWT, o Bearer e a credencial da URL antes do e-mail e dos
  // números; o CNPJ (14) antes do CPF (11).
  var MASCARAS_DE_DADO = [
    // Erro do Postgres repassado pela API traz a linha recusada inteira: do
    // `DETAIL:` em diante nada sai (nem a coluna do `Key (...)`); fora dele, o
    // valor do `Key (coluna)=(valor)` some e a coluna fica.
    [/DETAIL:[\s\S]*/g, "DETAIL: [Filtrado]"],
    [/Failing row contains \([\s\S]*/g, "Failing row contains ([Filtrado])"],
    [/(\bKey \([^)]*\))=\((?:[^()]|\([^()]*(?:\)|$))*(?:\)|$)/g, "$1=([Filtrado])"],
    [/\bbearer\s+[^\s,;"']+/gi, "Bearer [token]"],
    [/\bAppKey\s+[^\s,;"']+/g, "AppKey [token]"],
    // Sem exigir os dois pontos: um JWT cortado ao meio continua reconhecível.
    [/\beyJ[\w-]+(?:\.[\w-]*)*/g, "[token]"],
    // Usuário e senha dentro de URL. Antes do e-mail, que comeria só um pedaço.
    [/(\b[A-Za-z][A-Za-z0-9+.-]{0,20}:\/\/)[^\s/@"'<>]{1,256}@/g, "$1[credencial]@"],
    // Partes com teto (64 e 253, os do RFC): sem ele a regex fica quadrática. Com
    // a letra acentuada: com só A-Z, "joão@..." deixaria o "joã" para trás.
    [/[\p{L}\p{N}_.%+-]{1,64}@[\p{L}\p{N}_.-]{1,253}\.[A-Za-z]{2,24}/gu, "[e-mail]"],
    [/\b[A-Z0-9]{2}\.[A-Z0-9]{3}\.[A-Z0-9]{3}\/[A-Z0-9]{4}-\d{2}\b/g, "[CNPJ]"],
    [/\b[A-Z0-9]{12}\d{2}\b/g, "[CNPJ]"],
    [/\b\d{3}\.\d{3}\.\d{3}-\d{2}\b/g, "[CPF]"],
    [/\b\d{9}-\d{2}\b/g, "[CPF]"],
    [/\b\d{11}\b/g, "[CPF]"],
    [/\(\d{2}\)\s?\d{4,5}-?\d{4}\b/g, "[telefone]"],
    [/\+55\s?\(?\d{2}\)?\s?\d{4,5}-?\d{4}\b/g, "[telefone]"],
    [/\b[1-9]\d\s(?:9\d{4}|[2-5]\d{3})-\d{4}\b/g, "[telefone]"],
  ];

  var PROFUNDIDADE = 20;

  // Teto de cada texto: a limpeza roda no aparelho do consultor. As máscaras
  // rodam sobre um pouco mais que isso (a folga), e só depois vem o corte final.
  // (O SDK do navegador não corta nada antes: `maxValueLength` não é usado.)
  var TAMANHO_MAXIMO = 16384;
  var FOLGA_DAS_MASCARAS = 1024;

  // Linhas do código em volta do erro: passam pelas máscaras de dado, mas não
  // pelas de chave em texto.
  var CONTEXTO_DE_CODIGO = new Set(["pre_context", "context_line", "post_context"]);

  // Caracteres de que CPF, CNPJ, e-mail, JWT e URL são feitos.
  var CARACTERE_DE_DADO = /[\p{L}\p{N}_.%+\-@/=:]/u;

  /**
   * Tira do fim a sequência de caracteres de dado que o corte pode ter partido:
   * um "123.456.7" ou um "fulana@empre" já não casam com a máscara.
   */
  function semRestoCortado(texto) {
    var fim = texto.length;
    while (fim > 0 && CARACTERE_DE_DADO.test(texto[fim - 1])) fim -= 1;
    return texto.slice(0, fim).trimEnd();
  }

  /**
   * Troca CNPJ, CPF, e-mail, telefone e token dentro de um texto qualquer. O
   * corte vem DEPOIS das máscaras, e o que um corte partir ao meio sai inteiro.
   * `codigo` (linha de código de um frame) pula as máscaras de chave em texto.
   */
  function mascararTexto(texto, codigo) {
    var atual = texto;
    var cortado = false;
    if (atual.length > TAMANHO_MAXIMO + FOLGA_DAS_MASCARAS) {
      atual = semRestoCortado(atual.slice(0, TAMANHO_MAXIMO + FOLGA_DAS_MASCARAS));
      cortado = true;
    }
    var grupos = codigo ? [MASCARAS_DE_DADO] : [MASCARAS_DE_CHAVE, MASCARAS_DE_DADO];
    grupos.forEach(function (grupo) {
      grupo.forEach(function (mascara) {
        atual = atual.replace(mascara[0], mascara[1]);
      });
    });
    if (atual.length > TAMANHO_MAXIMO) {
      atual = semRestoCortado(atual.slice(0, TAMANHO_MAXIMO));
      cortado = true;
    }
    return cortado ? atual + " [cortado]" : atual;
  }

  function semQuery(url) {
    if (typeof url !== "string") return url;
    return url.split("?")[0].split("#")[0];
  }

  function varrer(valor, profundidade, codigo) {
    profundidade = profundidade || 0;
    if (profundidade > PROFUNDIDADE) return FILTRADO;
    if (typeof valor === "string") return mascararTexto(valor, codigo);
    if (Array.isArray(valor)) {
      return valor.map(function (item) {
        return varrer(item, profundidade + 1, codigo);
      });
    }
    if (valor && typeof valor === "object") {
      var limpo = {};
      Object.keys(valor).forEach(function (chave) {
        if (CHAVE_SENSIVEL.test(chave)) limpo[chave] = FILTRADO;
        else limpo[chave] = varrer(valor[chave], profundidade + 1, codigo || CONTEXTO_DE_CODIGO.has(chave));
      });
      return limpo;
    }
    return valor;
  }

  function limparRequisicao(requisicao) {
    if (!requisicao || typeof requisicao !== "object") return undefined;
    var cabecalhos = {};
    Object.keys(requisicao.headers || {}).forEach(function (nome) {
      if (CABECALHOS_PERMITIDOS.has(nome.toLowerCase())) {
        var valor = requisicao.headers[nome];
        cabecalhos[nome] = nome.toLowerCase() === "referer" ? semQuery(valor) : valor;
      }
    });
    var limpa = { url: semQuery(requisicao.url), method: requisicao.method, headers: cabecalhos };
    Object.keys(limpa).forEach(function (chave) {
      if (!limpa[chave]) delete limpa[chave];
    });
    return limpa;
  }

  function limparDadosDeRastro(dados) {
    if (!dados || typeof dados !== "object") return dados;
    var limpos = Object.assign({}, dados);
    ["url", "to", "from"].forEach(function (campo) {
      if (campo in limpos) limpos[campo] = semQuery(limpos[campo]);
    });
    return limpos;
  }

  /** `beforeBreadcrumb`: rastro de rede e de navegação sem query string. */
  function limparRastro(rastro) {
    if (!rastro) return rastro;
    if (rastro.category === "console" || String(rastro.category || "").indexOf("ui.") === 0) return null;
    return Object.assign({}, rastro, { data: limparDadosDeRastro(rastro.data) });
  }

  // O arquivo de cada frame é a URL da página (o código do Lobby roda inline,
  // transpilado pelo Babel): no definir-senha, com o token na query.
  function limparFrames(evento) {
    var valores = (evento.exception && evento.exception.values) || [];
    valores.forEach(function (valor) {
      var frames = (valor && valor.stacktrace && valor.stacktrace.frames) || [];
      frames.forEach(function (frame) {
        if (!frame || typeof frame !== "object") return;
        delete frame.vars;
        if ("filename" in frame) frame.filename = semQuery(frame.filename);
        if ("abs_path" in frame) frame.abs_path = semQuery(frame.abs_path);
      });
    });
  }

  // Promessa rejeitada com objeto: o SDK o serializa em `extra.__serialized__`;
  // só sai o que descreve o erro.
  var SERIALIZADO_PERMITIDO = ["name", "message", "code", "status"];

  function limparSerializado(extra) {
    if (!extra || typeof extra !== "object" || !("__serialized__" in extra)) return extra;
    var serializado = extra.__serialized__;
    var permitido = FILTRADO;
    if (serializado && typeof serializado === "object") {
      permitido = {};
      SERIALIZADO_PERMITIDO.forEach(function (campo) {
        if (campo in serializado) permitido[campo] = serializado[campo];
      });
    }
    return Object.assign({}, extra, { __serialized__: permitido });
  }

  /** `beforeSend`: o evento sai sem dado pessoal (ver o cabeçalho do arquivo). */
  function limparEvento(evento) {
    if (!evento || typeof evento !== "object") return evento;
    var copia = Object.assign({}, evento);
    delete copia.user;
    if ("extra" in copia) copia.extra = limparSerializado(copia.extra);
    if ("request" in copia) {
      var requisicao = limparRequisicao(copia.request);
      if (requisicao) copia.request = requisicao;
      else delete copia.request;
    }
    if (Array.isArray(copia.breadcrumbs)) {
      copia.breadcrumbs = copia.breadcrumbs.map(function (rastro) {
        return Object.assign({}, rastro, { data: limparDadosDeRastro(rastro && rastro.data) });
      });
    }
    // Os frames são limpos na cópia que a varredura devolve, e não no evento que
    // o SDK passou: o resto desta função também não mexe no original.
    var limpo = varrer(copia);
    limparFrames(limpo);
    return limpo;
  }

  /** As opções do `Sentry.init`. Separadas para o teste conferir cada uma. */
  function opcoes(config, Sentry) {
    return {
      dsn: String(config.dsn).trim(),
      release: String(config.release || "").trim() || undefined,
      environment: String(config.environment || "").trim() || "production",
      sendDefaultPii: false,
      // Nesta versão do SDK é o `dataCollection` que decide o que se coleta, e o
      // padrão dele é coletar tudo, inclusive pedir ao servidor que grave o IP.
      // Tudo fechado, explícito (o mesmo do Conecta).
      dataCollection: {
        userInfo: false,
        cookies: false,
        httpHeaders: false,
        httpBodies: [],
        urlQueryParams: false,
        graphQL: { document: false, variables: false },
        genAI: { inputs: false, outputs: false },
        databaseQueryData: false,
        queues: false,
        stackFrameVariables: false,
      },
      sendClientReports: false,
      // Sem `tracesSampleRate`: tracing desligado. Lista fechada no lugar das
      // padrão, que trazem console, sessões e contexto de idioma.
      defaultIntegrations: false,
      integrations: [
        Sentry.eventFiltersIntegration(),
        Sentry.functionToStringIntegration(),
        Sentry.browserApiErrorsIntegration(),
        Sentry.breadcrumbsIntegration({ console: false, dom: false }),
        Sentry.globalHandlersIntegration(),
        Sentry.linkedErrorsIntegration(),
        Sentry.dedupeIntegration(),
        Sentry.httpContextIntegration(),
      ],
      beforeBreadcrumb: limparRastro,
      beforeSend: limparEvento,
    };
  }

  /**
   * Dispara um erro não tratado de dentro deste arquivo, para conferir o
   * GlitchTip depois do deploy: no console do navegador,
   * `lobbyErroDeTesteGlitchtip()`. O `setTimeout` faz o erro nascer aqui, e não
   * no console, como um erro de verdade da página.
   */
  function erroDeTeste() {
    setTimeout(function () {
      throw new Error("Erro de teste do GlitchTip (TSK-1142), disparado no console");
    }, 0);
  }

  /**
   * Liga o SDK. Sem DSN, ou sem o SDK carregado, não faz nada e devolve false.
   * Nunca levanta: um GlitchTip mal configurado não pode parar a venda.
   */
  function iniciar(config, Sentry, janela) {
    var dsn = config && typeof config.dsn === "string" ? config.dsn.trim() : "";
    if (!dsn) return false;
    if (!Sentry || typeof Sentry.init !== "function") return false;
    try {
      Sentry.init(opcoes(config, Sentry));
    } catch (erro) {
      if (typeof console !== "undefined") console.warn("[GlitchTip] desligado:", erro && erro.name);
      return false;
    }
    if (janela) janela.lobbyErroDeTesteGlitchtip = erroDeTeste;
    return true;
  }

  var api = {
    FILTRADO: FILTRADO,
    mascararTexto: mascararTexto,
    limparEvento: limparEvento,
    limparRastro: limparRastro,
    opcoes: opcoes,
    iniciar: iniciar,
  };

  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (raiz && raiz.document) {
    raiz.LobbyGlitchtip = api;
    api.iniciar(raiz.LOBBY_GLITCHTIP, raiz.Sentry, raiz);
  }
})(typeof window !== "undefined" ? window : undefined);
