// O sinal de presença da tela (TSK-1520), sem navegador.
//
//     node --test tests/front/presenca.test.mjs
//
// O painel "Uso em tempo real" do Conecta mostra quem está online em cada
// sistema, e em qual tela. O teste roda os trechos de JS puro do próprio
// index.html — a sessão e o sinal — com `fetch`, `sessionStorage`, `document` e
// relógio de mentira, e confere:
//
// - o texto de cada tela ("módulo · tela"), curto e sem dado do cliente;
// - o pedido: POST /api/presenca, com a sessão, a tela e a última interação em
//   `toISOString()`; sem sessão (ou vencida), nada sai;
// - o erro engolido: rede, 5xx, 429, 401 e o pedido pendurado viram `false`,
//   sem lançar, sem derrubar a sessão e sem repetir; depois de um 401, o mesmo
//   token cala;
// - o relógio: um sinal por minuto, um na troca de tela (o redirecionamento
//   manda um só), um na volta à aba; desligado (sem login), nada;
// - a última interação: clique, tecla, roda, dedo e Voltar/Avançar; `scroll`, não;
// - no App, o sinal só liga com o lobby aberto para alguém, e as telas de
//   senha nem têm o código.
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const raiz = new URL('../../', import.meta.url);
const html = readFileSync(new URL('index.html', raiz), 'utf8');

function entre(texto, inicio, fim, { incluiFim = false } = {}) {
  const i = texto.indexOf(inicio);
  const f = texto.indexOf(fim, i + 1);
  assert.ok(i > 0 && f > i, `o trecho "${inicio.slice(0, 50)}" sumiu do index.html — ajuste os marcadores`);
  return texto.slice(i, incluiFim ? f + fim.length : f);
}

const SESSAO = entre(html, '/* ===== Sessão do Lobby (TSK-1513) ===== */', '/* ===== fim da sessão do Lobby ===== */', { incluiFim: true });
const SINAL = entre(html, '/* ===== Sinal de presença (TSK-1520) ===== */', '/* ===== fim do sinal de presença ===== */', { incluiFim: true });

const EXPOSTOS = [
  'sessaoLobby', 'iniciaSessao', 'encerraSessao',
  'PRESENCA_ROTA', 'PRESENCA_INTERVALO_MS', 'PRESENCA_ESPERA_NA_TROCA_MS', 'PRESENCA_VOLTA_SEM_SINAL_MS',
  'PRESENCA_TEMPO_MAXIMO_MS', 'telaDaPresenca', 'enviaSinalDePresenca', 'criaSinalDePresenca', 'instalaSinalDePresenca',
];

// ----- o mundo de mentira -----------------------------------------------------

const resposta = (status) => ({ status, ok: status >= 200 && status < 300, json: async () => ({}) });

function fetchDeMentira(regra = () => resposta(204)) {
  const chamadas = [];
  const f = async (url, init = {}) => {
    chamadas.push({ url, init, headers: init.headers || {}, corpo: init.body ? JSON.parse(init.body) : null });
    return regra(url, init);
  };
  f.chamadas = chamadas;
  return f;
}

function guardaDeMentira() {
  const mapa = new Map();
  return { getItem: (k) => (mapa.has(k) ? mapa.get(k) : null), setItem: (k, v) => mapa.set(k, String(v)), removeItem: (k) => mapa.delete(k) };
}

// O `document` e a `window` do navegador: só eventos e o `visibilityState`.
function paginaDeMentira() {
  const pagina = new EventTarget();
  pagina.visibilityState = 'visible';
  return pagina;
}

function lobby(regra) {
  const fetch = fetchDeMentira(regra);
  const document = paginaDeMentira();
  const window = new EventTarget();
  const api = new Function(
    'fetch', 'sessionStorage', 'document', 'window',
    `${SESSAO}\n${SINAL}\nreturn { ${EXPOSTOS.join(', ')} };`,
  )(fetch, guardaDeMentira(), document, window);
  const quedas = [];
  api.sessaoLobby.aoDerrubar = (motivo) => quedas.push(motivo);
  return { ...api, fetch, document, window, quedas };
}

const daquiAUmaHora = () => Math.floor(Date.now() / 1000) + 3600;
const LOGIN = () => ({ token: 'eyJ.sessao.da-carla', exp: daquiAUmaHora(), nome: 'Carla', email: 'carla@sejaap.com.br', papel: '', podeAutorizarNegociacao: false });
const ELITE = { modulo: 'Elite', tela: 'Pagamento · ELITE PRO' };
const LOBBY = { modulo: 'Lobby', tela: 'Categorias' };
const INICIO = Date.parse('2026-11-17T13:00:00.000Z');

// Relógio de mentira (setTimeout e Date), desligado ao fim de cada teste. Anda
// em passos de 100 ms: o `tick` do Node leva o `Date.now()` ao fim do salto antes
// de disparar os timers vencidos no caminho, e um salto de minutos faria o
// relógio do sinal "acordar" atrasado, como nenhum navegador faz.
function comRelogio(t) {
  mock.timers.enable({ apis: ['setTimeout', 'Date'], now: INICIO });
  t.after(() => mock.timers.reset());
  return (ms) => {
    for (let falta = ms; falta > 0; falta -= 100) mock.timers.tick(Math.min(100, falta));
  };
}

// Deixa as promessas pendentes andarem (o `fetch` de mentira é assíncrono).
const assenta = () => new Promise((r) => setImmediate(r));

// ----- o texto de cada tela ---------------------------------------------------

test('o texto de cada tela: "módulo · tela", com a categoria, a etapa e o produto', () => {
  const { telaDaPresenca } = lobby();
  const casos = [
    [{ tela: 'lobby', categoria: 'Elite' }, LOBBY],
    [{ tela: 'flow', categoria: 'Elite', etapa: 'Empresa' }, { modulo: 'Elite', tela: 'Empresa' }],
    [{ tela: 'flow', categoria: 'Elite', etapa: 'Pagamento', produto: 'ELITE PRO' }, ELITE],
    [{ tela: 'flow', categoria: 'APN', etapa: 'Cliente' }, { modulo: 'APN', tela: 'Cliente' }],
    [{ tela: 'flow', categoria: 'Produtos', etapa: 'Produto' }, { modulo: 'Produtos', tela: 'Produto' }],
    [{ tela: 'flow', categoria: 'Produtos', etapa: 'Quadro comercial', produto: 'Recrutamento e Seleção' },
      { modulo: 'Produtos', tela: 'Quadro comercial · Recrutamento e Seleção' }],
    [{ tela: 'flow', categoria: 'Produtos', etapa: 'Itens', produto: 'DH - Avaliação' }, { modulo: 'Produtos', tela: 'Itens · DH - Avaliação' }],
    [{ tela: 'flow', categoria: 'Elite', etapa: 'Produto', produto: 'ELITE PRO', autorizando: true },
      { modulo: 'Elite', tela: 'Autorização de negociação · ELITE PRO' }],
    [{ tela: 'success', categoria: 'APN', etapa: 'Resumo' }, { modulo: 'APN', tela: 'Venda enviada' }],
    [{ tela: 'success', categoria: 'Elite', etapa: 'Resumo', produto: 'ELITE PRO' }, { modulo: 'Elite', tela: 'Venda enviada · ELITE PRO' }],
    // Sem categoria (não deveria acontecer no fluxo): a grade, e não um texto vazio.
    [{ tela: 'flow', categoria: null, etapa: 'Empresa' }, LOBBY],
  ];
  for (const [onde, esperado] of casos) assert.deepEqual(telaDaPresenca(onde), esperado, JSON.stringify(onde));
});

test('o texto vai cortado no que o Conecta grava (60 e 120), numa linha só', () => {
  const { telaDaPresenca } = lobby();
  const longa = telaDaPresenca({ tela: 'flow', categoria: 'C'.repeat(80), etapa: 'Pagamento', produto: 'P'.repeat(200) });
  assert.equal(longa.modulo.length, 60);
  assert.ok(longa.modulo.endsWith('…'));
  assert.equal(longa.tela.length, 120);
  assert.ok(longa.tela.startsWith('Pagamento · PPP') && longa.tela.endsWith('…'));
  assert.deepEqual(telaDaPresenca({ tela: 'flow', categoria: ' Elite\n', etapa: 'Resumo', produto: 'ELITE\t PRO ' }), { modulo: 'Elite', tela: 'Resumo · ELITE PRO' });
});

test('as etapas de todos os fluxos cabem na tela, mesmo com o maior produto do catálogo', () => {
  const { telaDaPresenca } = lobby();
  const etapas = [...html.matchAll(/^const STEPS\w* = \[([^\]]+)\];/gm)].flatMap((m) => m[1].match(/'[^']+'/g).map((e) => e.slice(1, -1)));
  assert.ok(etapas.includes('Quadro comercial') && etapas.includes('Itens'), 'achou as etapas no index.html');
  const produtos = [...html.matchAll(/name: '([^']+)', sigla:/g)].map((m) => m[1]);
  const maior = produtos.sort((a, b) => b.length - a.length)[0];
  for (const etapa of etapas) {
    const t = telaDaPresenca({ tela: 'flow', categoria: 'Produtos', etapa, produto: maior });
    assert.equal(t.tela, `${etapa} · ${maior}`, 'sem corte nos nomes de hoje');
  }
});

// ----- o pedido ---------------------------------------------------------------

test('o sinal vai ao backend do Lobby com a sessão, a tela e a última interação', async () => {
  const l = lobby();
  l.iniciaSessao(LOGIN());
  const quando = Date.parse('2026-11-17T13:02:11.000Z');
  assert.equal(await l.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: quando }), true);
  const [chamada] = l.fetch.chamadas;
  assert.equal(chamada.url, '/api/presenca');
  assert.equal(chamada.init.method, 'POST');
  assert.equal(chamada.headers.Authorization, 'Bearer eyJ.sessao.da-carla');
  assert.equal(chamada.headers['Content-Type'], 'application/json');
  assert.deepEqual(chamada.corpo, { modulo: 'Elite', tela: 'Pagamento · ELITE PRO', ultima_interacao: '2026-11-17T13:02:11.000Z' });
  assert.equal(chamada.corpo.ultima_interacao, new Date(quando).toISOString());
});

test('a última interação que não é data não vai (e o sinal vai sem ela)', async () => {
  const l = lobby();
  l.iniciaSessao(LOGIN());
  for (const ruim of [NaN, null, undefined, 9e15]) await l.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: ruim });
  assert.equal(l.fetch.chamadas.length, 4);
  for (const c of l.fetch.chamadas) assert.deepEqual(c.corpo, { modulo: 'Elite', tela: 'Pagamento · ELITE PRO' });
});

test('sem sessão, com a sessão vencida, ou sem módulo: nada sai, e nada cai', async () => {
  const semSessao = lobby();
  assert.equal(await semSessao.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: Date.now() }), false);
  assert.equal(semSessao.fetch.chamadas.length, 0);

  const vencida = lobby();
  vencida.iniciaSessao({ ...LOGIN(), exp: Math.floor(Date.now() / 1000) - 5 });
  assert.equal(await vencida.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: Date.now() }), false);
  assert.equal(vencida.fetch.chamadas.length, 0);
  assert.deepEqual(vencida.quedas, [], 'o sinal não derruba a sessão: quem cuida dela são as chamadas da tela');
  assert.ok(vencida.sessaoLobby.atual);

  const semModulo = lobby();
  semModulo.iniciaSessao(LOGIN());
  assert.equal(await semModulo.enviaSinalDePresenca({ modulo: '', tela: 'x', ultimaInteracao: Date.now() }), false);
  assert.equal(semModulo.fetch.chamadas.length, 0);
});

test('o erro é engolido: rede, 5xx, 429 e 400 viram false, sem lançar e sem derrubar a sessão', async () => {
  for (const regra of [
    () => { throw new TypeError('Failed to fetch'); },
    () => resposta(502), () => resposta(500), () => resposta(429), () => resposta(400), () => resposta(403),
  ]) {
    const l = lobby(regra);
    l.iniciaSessao(LOGIN());
    assert.equal(await l.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: Date.now() }), false);
    assert.equal(l.fetch.chamadas.length, 1, 'uma tentativa só, sem repetir');
    assert.deepEqual(l.quedas, []);
    assert.ok(l.sessaoLobby.atual);
  }
});

test('o 401 do sinal não derruba a sessão, e cala o mesmo token até a sessão mudar', async () => {
  const l = lobby((url, init) => resposta(init.headers.Authorization === 'Bearer eyJ.sessao.da-carla' ? 401 : 204));
  l.iniciaSessao(LOGIN());
  assert.equal(await l.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: Date.now() }), false);
  assert.deepEqual(l.quedas, [], 'nem queda, nem login');
  assert.equal(l.sessaoLobby.atual.token, 'eyJ.sessao.da-carla');

  assert.equal(await l.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: Date.now() }), false);
  assert.equal(l.fetch.chamadas.length, 1, 'o token recusado não manda de novo');

  l.encerraSessao();
  l.iniciaSessao({ ...LOGIN(), token: 'eyJ.sessao.nova' });
  assert.equal(await l.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: Date.now() }), true);
  assert.equal(l.fetch.chamadas.length, 2);
});

test('o pedido pendurado é cortado em 10 s, sem lançar', async (t) => {
  const anda = comRelogio(t);
  const l = lobby((url, init) => new Promise((_, rejeita) => {
    init.signal.addEventListener('abort', () => rejeita(new DOMException('cortado', 'AbortError')));
  }));
  l.iniciaSessao(LOGIN());
  const envio = l.enviaSinalDePresenca({ ...ELITE, ultimaInteracao: Date.now() });
  await assenta();
  anda(l.PRESENCA_TEMPO_MAXIMO_MS);
  assert.equal(await envio, false);
  assert.equal(l.PRESENCA_TEMPO_MAXIMO_MS, 10 * 1000);
});

test('o sinal não passa pelo apiFetch, não derruba sessão e não mostra nada', () => {
  const corpo = entre(SINAL, 'async function enviaSinalDePresenca', 'function criaSinalDePresenca');
  for (const proibido of ['apiFetch(', 'derrubaSessao(', 'encerraSessao(', 'alert(', 'confirm(', 'console.', 'setS(', 'patch(']) {
    assert.ok(!corpo.includes(proibido), `o sinal não pode chamar ${proibido}`);
  }
  assert.ok(!/localStorage|sessionStorage/.test(SINAL), 'nada do sinal fica guardado no navegador');
});

// ----- o relógio --------------------------------------------------------------

function relogioComEspiao(t) {
  const anda = comRelogio(t);
  const l = lobby();
  const enviados = [];
  const sinal = l.criaSinalDePresenca({ envia: (s) => enviados.push({ ...s, em: Date.now() - INICIO }), visivel: () => l.document.visibilityState === 'visible' });
  return { l, sinal, enviados, anda };
}

test('um sinal por minuto com a aba aberta, o primeiro logo ao ligar', (t) => {
  const { l, sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(ELITE);
  anda(l.PRESENCA_ESPERA_NA_TROCA_MS - 1);
  assert.equal(enviados.length, 0);
  anda(1);
  assert.deepEqual(enviados.map((e) => [e.em, e.modulo, e.tela]), [[500, 'Elite', 'Pagamento · ELITE PRO']]);

  anda(l.PRESENCA_INTERVALO_MS - 1);
  assert.equal(enviados.length, 1);
  anda(1);
  assert.equal(enviados.length, 2);
  anda(3 * l.PRESENCA_INTERVALO_MS);
  assert.deepEqual(enviados.map((e) => e.em), [500, 60500, 120500, 180500, 240500]);
  assert.equal(l.PRESENCA_INTERVALO_MS, 60 * 1000);
});

test('a aba em segundo plano segue mandando (o relógio não para ao esconder)', (t) => {
  const { l, sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(ELITE);
  anda(500);
  l.document.visibilityState = 'hidden';
  l.document.dispatchEvent(new Event('visibilitychange'));
  anda(3 * 60 * 1000);
  assert.equal(enviados.length, 4);
});

test('trocar de tela manda na hora, e o minuto recomeça dali', (t) => {
  const { sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(LOBBY);
  anda(500);
  anda(20 * 1000);
  sinal.em(ELITE);
  anda(500);
  assert.deepEqual(enviados.map((e) => [e.em, e.tela]), [[500, 'Categorias'], [21000, 'Pagamento · ELITE PRO']]);
  anda(60 * 1000 - 1);
  assert.equal(enviados.length, 2, 'o minuto conta da troca, e não do primeiro sinal');
  anda(1);
  assert.deepEqual(enviados.at(-1).em, 81000);
});

test('um redirecionamento manda um sinal só, o da tela onde a pessoa ficou', (t) => {
  const { sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(LOBBY);
  anda(100);
  sinal.em({ modulo: 'Elite', tela: 'Empresa' });
  anda(100);
  sinal.em(ELITE);
  anda(500);
  anda(10 * 1000);
  assert.deepEqual(enviados.map((e) => e.tela), ['Pagamento · ELITE PRO']);
});

test('a mesma tela de novo (um render qualquer) não manda nem atrasa o relógio', (t) => {
  const { sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(ELITE);
  anda(500);
  for (let i = 0; i < 5; i++) { anda(10 * 1000); sinal.em({ ...ELITE }); }
  anda(10 * 1000);
  assert.deepEqual(enviados.map((e) => e.em), [500, 60500]);
});

test('sem login (desligado), nada sai — nem pelo relógio, nem pela volta à aba', (t) => {
  const { l, sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(null);
  sinal.interagiu();
  l.document.visibilityState = 'hidden';
  anda(60 * 1000);
  l.document.visibilityState = 'visible';
  sinal.voltouAAba();
  anda(5 * 60 * 1000);
  assert.deepEqual(enviados, []);
});

test('sair (ou cair) para o relógio, inclusive o sinal da troca que ainda não tinha saído', (t) => {
  const { sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(ELITE);
  anda(500);
  sinal.em(LOBBY);
  anda(100);
  sinal.em(null);   // a tela de login
  anda(5 * 60 * 1000);
  assert.deepEqual(enviados.map((e) => e.tela), ['Pagamento · ELITE PRO']);
  // E entrar de novo liga na hora.
  sinal.em(LOBBY);
  anda(500);
  assert.equal(enviados.at(-1).tela, 'Categorias');
});

test('a volta à aba manda na hora (se o último saiu há 30 s ou mais), e o minuto recomeça', (t) => {
  const { l, sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(ELITE);
  anda(500);
  l.document.visibilityState = 'hidden';
  anda(20 * 1000);
  l.document.visibilityState = 'visible';
  sinal.voltouAAba();
  assert.equal(enviados.length, 1, 'há 20 s: o painel já está em dia');

  l.document.visibilityState = 'hidden';
  anda(15 * 1000);
  l.document.visibilityState = 'visible';
  sinal.voltouAAba();
  assert.deepEqual(enviados.map((e) => e.em), [500, 35500]);
  anda(60 * 1000 - 1);
  assert.equal(enviados.length, 2);
  anda(1);
  assert.equal(enviados.at(-1).em, 95500);

  // Esconder não manda nada.
  anda(40 * 1000);
  l.document.visibilityState = 'hidden';
  sinal.voltouAAba();
  assert.equal(enviados.length, 3);
});

test('a última interação: a abertura, até o primeiro clique; depois, o último', (t) => {
  const { sinal, enviados, anda } = relogioComEspiao(t);
  sinal.em(ELITE);
  anda(500);
  assert.equal(enviados[0].ultimaInteracao, INICIO, 'antes de qualquer clique, vale a abertura da página');
  anda(10 * 1000);
  sinal.interagiu();
  anda(50 * 1000);
  assert.equal(enviados[1].ultimaInteracao, INICIO + 10500);
  anda(60 * 1000);
  assert.equal(enviados[2].ultimaInteracao, INICIO + 10500, 'o sinal do minuto não é interação');
});

test('clique, tecla, roda, dedo e Voltar/Avançar contam; a rolagem da própria página, não', (t) => {
  const anda = comRelogio(t);
  const l = lobby();
  const enviados = [];
  const sinal = l.instalaSinalDePresenca(l.criaSinalDePresenca({ envia: (s) => enviados.push(s) }), l.document, l.window);
  sinal.em(ELITE);
  anda(500);
  const ultima = () => { sinal.voltouAAba(); return enviados.at(-1).ultimaInteracao; };

  for (const [alvo, evento] of [[l.document, 'pointerdown'], [l.document, 'keydown'], [l.document, 'wheel'], [l.document, 'touchmove'], [l.window, 'popstate']]) {
    anda(40 * 1000);
    const agora = Date.now();
    alvo.dispatchEvent(new Event(evento));
    assert.equal(ultima(), agora, evento);
  }
  anda(40 * 1000);
  const antes = enviados.at(-1).ultimaInteracao;
  l.document.dispatchEvent(new Event('scroll'));
  assert.equal(ultima(), antes, 'scroll não é a pessoa');

  // E o `visibilitychange` da página é a volta à aba.
  anda(40 * 1000);
  const quantos = enviados.length;
  l.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(enviados.length, quantos + 1);
});

test('de ponta a ponta no JS: entrar liga, cada minuto vai ao backend, sair cala', async (t) => {
  const anda = comRelogio(t);
  const l = lobby();
  const sinal = l.criaSinalDePresenca({ visivel: () => true });   // com o `enviaSinalDePresenca` de verdade

  // A tela de login: o App diz null, e nada sai.
  sinal.em(null);
  anda(3 * 60 * 1000);
  await assenta();
  assert.equal(l.fetch.chamadas.length, 0);

  l.iniciaSessao(LOGIN());
  sinal.em(LOBBY);
  anda(500);
  await assenta();
  anda(60 * 1000);
  await assenta();
  assert.deepEqual(l.fetch.chamadas.map((c) => c.corpo.tela), ['Categorias', 'Categorias']);
  assert.ok(l.fetch.chamadas.every((c) => c.url === '/api/presenca'));

  l.encerraSessao();
  sinal.em(null);
  anda(5 * 60 * 1000);
  await assenta();
  assert.equal(l.fetch.chamadas.length, 2);
});

// ----- no App -----------------------------------------------------------------

test('no App, o sinal só liga com o lobby aberto para alguém (logado), antes do login no render', () => {
  const app = entre(html, 'function App({ motivoAoAbrir })', 'function carregaCatalogo');
  assert.match(app, /const logado = temSessao && catalogoPronto;/);
  const liga = app.indexOf('const presenca = logado\n    ? telaDaPresenca(');
  assert.ok(liga > 0, 'a tela de agora sai do `logado`');
  assert.match(app, /useEffect\(\(\) => \{ sinalDePresenca\.em\(presenca\); \}, \[presenca && presenca\.modulo, presenca && presenca\.tela\]\);/);
  // O hook vem antes dos retornos do render (regra dos hooks), e o login é o primeiro deles.
  assert.ok(liga < app.indexOf('if (!sessao) return <TelaDeLogin'));
  // Uma instância por aba, ligada aos eventos da página.
  assert.equal(html.match(/instalaSinalDePresenca\(criaSinalDePresenca\(\)\)/g).length, 1);
});

test('as telas sem login (esqueci e definir a senha) não mandam sinal', () => {
  for (const pagina of ['esqueci-senha.html', 'definir-senha.html']) {
    const texto = readFileSync(new URL(pagina, raiz), 'utf8');
    assert.ok(!texto.includes('/api/presenca') && !texto.includes('SinalDePresenca'), pagina);
  }
});
