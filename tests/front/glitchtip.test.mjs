// Erros do navegador no GlitchTip sem dado pessoal (TSK-1142), sem navegador.
//
//     node --test tests/front/glitchtip.test.mjs
//
// Confere, nesta ordem:
// - o SDK versionado é o arquivo oficial da Sentry, byte a byte (sha256), e os
//   três HTMLs o carregam antes do React;
// - o `beforeSend` (`limparEvento`) mascara CNPJ, senha, Authorization e o resto
//   da lista, e tira a query das URLs (o link de definir senha leva o token);
// - as opções do SDK: PII, coleta, tracing e integrações fechados;
// - sem DSN nada liga; com DSN ruim, nada quebra;
// - o `config.js` que o container escreve na subida não vira injeção de script;
// - e o SDK DE VERDADE, numa VM do Node com uma janela de mentira, manda o evento
//   já limpo para um transporte que só guarda o envelope (sem rede).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import vm from 'node:vm';

const raiz = new URL('../../', import.meta.url);
const ler = (caminho) => readFileSync(new URL(caminho, raiz));
const lobby = createRequire(import.meta.url)(new URL('glitchtip/lobby.js', raiz).pathname);

// https://browser.sentry-cdn.com/11.8.0/bundle.min.js, baixado em 20/10/2026.
// Trocar de versão: baixe o novo, troque o nome do arquivo nos três HTMLs e no
// deploy/frontend/Dockerfile, e este sha256.
const SDK = 'glitchtip/sentry-browser-11.8.0.min.js';
const SDK_SHA256 = '8d0a0d8e5b5b10998d3023cffa39dc8390213de8b6d2a62fff0eeb2e596af5b7';

const CNPJ = '12.345.678/0001-95';
const CNPJ_LIMPO = '12345678000195';
const CPF = '123.456.789-09';
const EMAIL = 'fulana.cliente@empresa.com.br';
const SENHA = 'Senha-Secreta-123';
const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjo0Mn0.c2lnbmF0dXJh';
const TOKEN_DO_LINK = 'tok3nD0L1nkD3S3nh4';

// ----- o SDK e os HTMLs -------------------------------------------------------

test('o SDK versionado é o bundle oficial 11.8.0, byte a byte', () => {
  const sdk = ler(SDK);
  assert.equal(createHash('sha256').update(sdk).digest('hex'), SDK_SHA256);
  assert.match(sdk.toString('utf8', 0, 80), /^\/\*! @sentry\/browser 11\.8\.0 /);
});

test('os três HTMLs carregam config, SDK e limpeza antes do React', () => {
  const esperado = [
    '<script src="/glitchtip/config.js"></script>',
    '<script src="/glitchtip/sentry-browser-11.8.0.min.js"></script>',
    '<script src="/glitchtip/lobby.js"></script>',
  ];
  for (const pagina of ['index.html', 'definir-senha.html', 'esqueci-senha.html']) {
    const html = ler(pagina).toString('utf8');
    const posicoes = esperado.map((tag) => html.indexOf(tag));
    assert.ok(posicoes.every((p) => p > 0), `${pagina}: falta script do GlitchTip`);
    assert.deepEqual([...posicoes].sort((a, b) => a - b), posicoes, `${pagina}: ordem trocada`);
    assert.ok(posicoes[2] < html.indexOf('react.production.min.js'), `${pagina}: GlitchTip depois do React`);
  }
  const dockerfile = ler('deploy/frontend/Dockerfile').toString('utf8');
  assert.match(dockerfile, /COPY glitchtip\/lobby\.js glitchtip\/sentry-browser-11\.8\.0\.min\.js /);
});

// ----- a limpeza do evento ----------------------------------------------------

function eventoComDadoPessoal() {
  return {
    message: `Falha no envio do cliente ${CNPJ} (${EMAIL})`,
    exception: {
      values: [{
        type: 'Error',
        value: `{"cnpj": "${CNPJ_LIMPO}", "senha": "${SENHA}"} cpf ${CPF}`,
        stacktrace: {
          frames: [{
            filename: `https://lobby.sejaap.com.br/definir-senha?token=${TOKEN_DO_LINK}`,
            abs_path: `https://lobby.sejaap.com.br/definir-senha?token=${TOKEN_DO_LINK}#x`,
            function: 'enviar',
            vars: { senha: SENHA },
          }],
        },
      }],
    },
    request: {
      url: `https://lobby.sejaap.com.br/definir-senha?token=${TOKEN_DO_LINK}`,
      method: 'GET',
      headers: {
        Authorization: `Bearer ${JWT}`,
        Cookie: 'sessionid=abc123',
        'User-Agent': 'Mozilla/5.0',
        Referer: `https://lobby.sejaap.com.br/?cnpj=${CNPJ_LIMPO}`,
      },
      cookies: 'sessionid=abc123',
      data: { senha: SENHA },
    },
    user: { email: EMAIL, ip_address: '200.200.200.200' },
    extra: {
      __serialized__: { message: 'falhou', status: 500, config: { data: `{"cnpj":"${CNPJ_LIMPO}"}` },
        response: { data: { parcelas: [{ valor: 1234.56, vencimento: '2026-11-15' }] } } },
    },
    breadcrumbs: [
      { category: 'navigation', data: { from: `/definir-senha?token=${TOKEN_DO_LINK}`, to: '/' } },
      { category: 'fetch', data: { url: `/api/departamentos?cnpj=${CNPJ_LIMPO}`, method: 'GET' } },
    ],
  };
}

test('passo 1 do contrato: CNPJ, senha e Authorization saem mascarados', () => {
  const limpo = lobby.limparEvento(eventoComDadoPessoal());
  const texto = JSON.stringify(limpo);
  for (const dado of [CNPJ, CNPJ_LIMPO, SENHA, JWT, CPF, EMAIL, TOKEN_DO_LINK, 'abc123',
    '200.200.200.200', '1234.56', '2026-11-15']) {
    assert.ok(!texto.includes(dado), `sobrou ${dado}`);
  }
  assert.equal(limpo.message, 'Falha no envio do cliente [CNPJ] ([e-mail])');
  assert.equal(limpo.exception.values[0].value, '{"cnpj": "[Filtrado]", "senha": "[Filtrado]"} cpf [CPF]');
  assert.deepEqual(limpo.request, {
    url: 'https://lobby.sejaap.com.br/definir-senha',
    method: 'GET',
    headers: { 'User-Agent': 'Mozilla/5.0', Referer: 'https://lobby.sejaap.com.br/' },
  });
  assert.equal(limpo.user, undefined);
  const frame = limpo.exception.values[0].stacktrace.frames[0];
  assert.equal(frame.filename, 'https://lobby.sejaap.com.br/definir-senha');
  assert.equal(frame.abs_path, 'https://lobby.sejaap.com.br/definir-senha');
  assert.equal(frame.vars, undefined);
  assert.deepEqual(limpo.extra.__serialized__, { message: 'falhou', status: 500 });
  assert.equal(limpo.breadcrumbs[0].data.from, '/definir-senha');
  assert.equal(limpo.breadcrumbs[1].data.url, '/api/departamentos');
});

test('o evento original não é alterado (o SDK pode reusá-lo)', () => {
  const original = eventoComDadoPessoal();
  const copia = JSON.parse(JSON.stringify(original));
  lobby.limparEvento(original);
  assert.deepEqual(original, copia);
});

test('rastro de console e de clique não sai; o de rede sai sem query', () => {
  assert.equal(lobby.limparRastro({ category: 'console', message: EMAIL }), null);
  assert.equal(lobby.limparRastro({ category: 'ui.click', message: 'div.cliente' }), null);
  assert.deepEqual(lobby.limparRastro({ category: 'xhr', data: { url: `/api/x?email=${EMAIL}` } }).data,
    { url: '/api/x' });
});

test('credenciais e dados do Lobby em texto', () => {
  const casos = {
    'Authorization: AppKey lobby:s3gr3d0-do-app': 'Authorization: AppKey [token]',
    'redis://:s3nh4redis@redis:6379/0': 'redis://[credencial]@redis:6379/0',
    'https://chavepublica@erros.sejaap.com.br/3': 'https://[credencial]@erros.sejaap.com.br/3',
    '{"comprovante": "v1.abc.def", "razao_social": "Padaria LTDA"}':
      '{"comprovante": "[Filtrado]", "razao_social": "[Filtrado]"}',
    'faultstring: CNPJ 12ABC34501DE35 já cadastrado': 'faultstring: CNPJ [CNPJ] já cadastrado',
    'ligar (11) 91234-5678 ou +55 11 912345678': 'ligar [telefone] ou [telefone]',
    'Key (cnpj)=(12345678000195) already exists\nDETAIL: Failing row contains (1, Maria)':
      'Key (cnpj)=([Filtrado]) already exists\nDETAIL: [Filtrado]',
    // Protocolo da venda e URL do webhook não são dado pessoal: ficam legíveis.
    'protocolo PRO-260701005821 em https://n8n.sejaap.com.br/webhook/lobby-apn':
      'protocolo PRO-260701005821 em https://n8n.sejaap.com.br/webhook/lobby-apn',
  };
  for (const [texto, esperado] of Object.entries(casos)) {
    assert.equal(lobby.mascararTexto(texto), esperado, texto);
  }
});

test('linha de código passa pelas máscaras de dado e não pelas de chave', () => {
  const evento = lobby.limparEvento({
    exception: { values: [{ stacktrace: { frames: [{
      context_line: 'enviar({ senha: senhaDigitada, email: "fulana@empresa.com.br" })',
      post_context: ['const cnpj = limpar(cnpj=valor)'],
    }] } }] },
  });
  const frame = evento.exception.values[0].stacktrace.frames[0];
  assert.equal(frame.context_line, 'enviar({ senha: senhaDigitada, email: "[e-mail]" })');
  assert.deepEqual(frame.post_context, ['const cnpj = limpar(cnpj=valor)']);
});

test('dado na posição do corte não sai pela metade', () => {
  const limite = 16384 + 1024;
  for (const dado of [CPF, EMAIL, JWT]) {
    for (let parte = 1; parte < dado.length; parte += 1) {
      for (const posicao of [limite - parte, 16384 - parte]) {
        const texto = 'x '.repeat(limite).slice(0, posicao) + dado + ' fim'.repeat(limite / 4);
        const limpo = lobby.mascararTexto(texto);
        assert.ok(limpo.endsWith(' [cortado]'));
        assert.ok(!limpo.includes(dado.slice(0, parte)), `${dado.slice(0, 6)} parte ${parte}`);
      }
    }
  }
});

test('pior caso das regex dentro do limite', () => {
  const tamanho = 16384 + 1024;
  const casos = {
    json: '"token'.repeat(tamanho).slice(0, tamanho),
    formulario: 'token='.repeat(tamanho).slice(0, tamanho),
    objeto: "{'token': 'a', ".repeat(tamanho).slice(0, tamanho),
    postgres: 'Key ('.repeat(tamanho).slice(0, tamanho),
    alfanumerico: 'a'.repeat(tamanho),
    email: 'a.'.repeat(tamanho).slice(0, tamanho),
    url: 'a://'.repeat(tamanho).slice(0, tamanho),
    appkey: 'AppKey '.repeat(tamanho).slice(0, tamanho),
  };
  for (const [nome, texto] of Object.entries(casos)) {
    const inicio = performance.now();
    lobby.mascararTexto(texto);
    assert.ok(performance.now() - inicio < 200, `${nome} demorou`);
  }
});

// ----- as opções e a ligação ---------------------------------------------------

function sentryDeMentira() {
  const chamadas = [];
  const integracao = (nome) => (opcoes) => ({ nome, opcoes });
  return {
    chamadas,
    init: (opcoes) => chamadas.push(opcoes),
    eventFiltersIntegration: integracao('eventFilters'),
    functionToStringIntegration: integracao('functionToString'),
    browserApiErrorsIntegration: integracao('browserApiErrors'),
    breadcrumbsIntegration: integracao('breadcrumbs'),
    globalHandlersIntegration: integracao('globalHandlers'),
    linkedErrorsIntegration: integracao('linkedErrors'),
    dedupeIntegration: integracao('dedupe'),
    httpContextIntegration: integracao('httpContext'),
  };
}

test('opções do SDK: sem PII, sem coleta, sem tracing e integrações a dedo', () => {
  const Sentry = sentryDeMentira();
  const opcoes = lobby.opcoes({ dsn: ' https://chave@erros.sejaap.com.br/3 ', release: 'abc1234' }, Sentry);
  assert.equal(opcoes.dsn, 'https://chave@erros.sejaap.com.br/3');
  assert.equal(opcoes.release, 'abc1234');
  assert.equal(opcoes.environment, 'production');
  assert.equal(opcoes.sendDefaultPii, false);
  assert.equal(opcoes.sendClientReports, false);
  assert.equal(opcoes.defaultIntegrations, false);
  for (const chave of ['tracesSampleRate', 'tracesSampler', 'profilesSampleRate', 'replaysSessionSampleRate']) {
    assert.equal(chave in opcoes, false, chave);
  }
  const coleta = opcoes.dataCollection;
  assert.deepEqual(coleta.httpBodies, []);
  for (const chave of ['userInfo', 'cookies', 'httpHeaders', 'urlQueryParams', 'databaseQueryData', 'queues',
    'stackFrameVariables']) {
    assert.equal(coleta[chave], false, chave);
  }
  assert.deepEqual(opcoes.integrations.map((i) => i.nome), ['eventFilters', 'functionToString',
    'browserApiErrors', 'breadcrumbs', 'globalHandlers', 'linkedErrors', 'dedupe', 'httpContext']);
  assert.deepEqual(opcoes.integrations[3].opcoes, { console: false, dom: false });
  assert.equal(opcoes.beforeSend, lobby.limparEvento);
  assert.equal(opcoes.beforeBreadcrumb, lobby.limparRastro);
  assert.equal(lobby.opcoes({ dsn: 'x', environment: 'homolog' }, Sentry).environment, 'homolog');
  assert.equal(lobby.opcoes({ dsn: 'x', release: '' }, Sentry).release, undefined);
});

test('passo 2 do contrato: sem DSN, ou sem o SDK, nada liga', () => {
  const Sentry = sentryDeMentira();
  const janela = {};
  for (const config of [undefined, null, {}, { dsn: '' }, { dsn: '   ' }, { dsn: 42 }]) {
    assert.equal(lobby.iniciar(config, Sentry, janela), false);
  }
  assert.equal(lobby.iniciar({ dsn: 'https://chave@erros.sejaap.com.br/3' }, undefined, janela), false);
  assert.equal(lobby.iniciar({ dsn: 'https://chave@erros.sejaap.com.br/3' }, {}, janela), false);
  assert.equal(Sentry.chamadas.length, 0);
  assert.equal('lobbyErroDeTesteGlitchtip' in janela, false);
});

test('SDK que levanta no init não derruba a página', () => {
  const Sentry = { ...sentryDeMentira(), init: () => { throw new TypeError('dsn inválido'); } };
  const aviso = console.warn;
  console.warn = () => {};
  try {
    assert.equal(lobby.iniciar({ dsn: 'https://x' }, Sentry, {}), false);
  } finally {
    console.warn = aviso;
  }
});

test('com DSN liga e deixa o erro de teste no console', () => {
  const Sentry = sentryDeMentira();
  const janela = {};
  assert.equal(lobby.iniciar({ dsn: 'https://chave@erros.sejaap.com.br/3', release: 'abc1234' }, Sentry, janela), true);
  assert.equal(Sentry.chamadas.length, 1);
  assert.equal(typeof janela.lobbyErroDeTesteGlitchtip, 'function');
});

// ----- o config.js da subida ---------------------------------------------------

function configDaSubida(ambiente) {
  const pasta = mkdtempSync(join(tmpdir(), 'glitchtip-'));
  const destino = join(pasta, 'config.js');
  try {
    const log = execFileSync('sh', [new URL('deploy/frontend/glitchtip-config.sh', raiz).pathname], {
      env: { PATH: process.env.PATH, GLITCHTIP_CONFIG_JS: destino, ...ambiente },
      encoding: 'utf8',
    });
    const janela = {};
    vm.runInNewContext(readFileSync(destino, 'utf8'), { window: janela });
    // JSON de ida e volta: o objeto da VM é de outro realm, e o deepEqual estrito o recusaria.
    return { log, config: JSON.parse(JSON.stringify(janela.LOBBY_GLITCHTIP)) };
  } finally {
    rmSync(pasta, { recursive: true, force: true });
  }
}

test('config.js da subida: sem DSN, desligado', () => {
  const { log, config } = configDaSubida({});
  assert.deepEqual(config, { dsn: '', release: '', environment: 'production' });
  assert.match(log, /front desligado: sem SENTRY_DSN/);
});

test('config.js da subida: DSN, release e ambiente', () => {
  const { log, config } = configDaSubida({
    SENTRY_DSN: 'https://0123abcdef@erros.sejaap.com.br/3\n', GIT_COMMIT: 'feebe8f', SENTRY_ENVIRONMENT: 'production',
  });
  assert.deepEqual(config, { dsn: 'https://0123abcdef@erros.sejaap.com.br/3', release: 'feebe8f', environment: 'production' });
  assert.match(log, /front ligado \(release=feebe8f\)/);
  assert.doesNotMatch(log, /0123abcdef/);
});

test('config.js da subida: valor fora do formato não vira script', () => {
  for (const dsn of ['https://a@b.com/3"; alert(1); "', 'javascript:alert(1)//@b.com/3',
    'https://a@b.com/3</script><script>alert(1)</script>', 'https://erros.sejaap.com.br/3']) {
    const { log, config } = configDaSubida({ SENTRY_DSN: dsn });
    assert.equal(config.dsn, '', dsn);
    assert.match(log, /fora do formato de DSN/);
  }
  const { config } = configDaSubida({
    SENTRY_DSN: 'https://chave@erros.sejaap.com.br/3', GIT_COMMIT: 'abc"; alert(1); "', SENTRY_ENVIRONMENT: 'x"y',
  });
  assert.deepEqual(config, { dsn: 'https://chave@erros.sejaap.com.br/3', release: '', environment: 'production' });
});

// ----- o SDK de verdade --------------------------------------------------------

function janelaComSdk(href) {
  const janela = {
    addEventListener() {},
    removeEventListener() {},
    location: { href, origin: 'https://lobby.sejaap.com.br' },
    navigator: { userAgent: 'Mozilla/5.0 (teste)' },
    document: { referrer: '', addEventListener() {}, visibilityState: 'visible' },
    history: { pushState() {}, replaceState() {} },
    console: { ...console, log() {}, warn() {}, error() {} },
    setTimeout, clearTimeout, URL, URLSearchParams, TextEncoder, TextDecoder,
  };
  janela.window = janela;
  janela.self = janela;
  const contexto = vm.createContext(janela);
  vm.runInContext(ler(SDK).toString('utf8'), contexto, { filename: 'sentry-browser-11.8.0.min.js' });
  return contexto;
}

test('SDK de verdade: o erro não tratado chega limpo, com o release', async () => {
  const pagina = `https://lobby.sejaap.com.br/definir-senha?token=${TOKEN_DO_LINK}`;
  const janela = janelaComSdk(pagina);
  assert.equal(janela.Sentry.SDK_VERSION, '11.8.0');
  const envelopes = [];
  const transporte = () => ({ send: async (envelope) => { envelopes.push(envelope); return {}; }, flush: async () => true });
  janela.Sentry.init({
    ...lobby.opcoes({ dsn: 'https://chave@erros.exemplo.invalid/3', release: 'abc1234' }, janela.Sentry),
    transport: transporte,
  });

  // Um erro que nasce "no index.html" (o filename da VM é a URL da página) e sobe
  // até o handler global, como o de uma venda que quebra.
  const erro = vm.runInContext(
    `(function enviarVenda() { try { throw new Error('webhook recusou ${CNPJ} de ${EMAIL}: {"senha": "${SENHA}"}'); }`
    + ' catch (e) { return e; } })()',
    janela, { filename: pagina },
  );
  janela.onerror(`Uncaught Error: ${erro.message}`, pagina, 1, 1, erro);
  await janela.Sentry.flush(2000);

  const itens = envelopes.flatMap((envelope) => envelope[1]);
  assert.deepEqual(itens.map(([cabecalho]) => cabecalho.type), ['event']);
  const evento = itens[0][1];
  assert.equal(evento.release, 'abc1234');
  assert.equal(evento.environment, 'production');
  assert.equal(evento.exception.values[0].value, 'webhook recusou [CNPJ] de [e-mail]: {"senha": "[Filtrado]"}');
  assert.equal(evento.exception.values[0].mechanism.handled, false);
  const texto = JSON.stringify(envelopes);
  for (const dado of [CNPJ, EMAIL, SENHA, TOKEN_DO_LINK]) {
    assert.ok(!texto.includes(dado), `sobrou ${dado}`);
  }
  assert.equal(evento.user, undefined);
  const arquivos = evento.exception.values[0].stacktrace.frames.map((frame) => frame.filename);
  assert.ok(arquivos.includes('https://lobby.sejaap.com.br/definir-senha'), arquivos.join(', '));
});
