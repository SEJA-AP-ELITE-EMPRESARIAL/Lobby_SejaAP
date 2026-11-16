// A sessão do Lobby (TSK-1513), sem navegador.
//
//     node --test tests/front/sessao.test.mjs
//
// Desde 14/11/2026 o Lobby pede a conta do Conecta para qualquer uso. O teste
// roda os trechos de JS puro do próprio index.html — o login central em
// manutenção, a sessão, o comprovante, o modal do gerente e o envio ao n8n —
// com um `fetch` e um `sessionStorage` de mentira, e confere:
//
// - o login: o corpo, a senha errada (que não derruba quem está logado), a
//   manutenção;
// - o que fica guardado (só no sessionStorage, só seis campos) e a sessão vencida;
// - o 401: derruba a sessão e volta ao login com o motivo; rede e 5xx não;
// - o comprovante: sessão no `Authorization`, quem autoriza no
//   `X-Lobby-Autorizacao`; o 401 PARA a venda; rede segue a regra degradada;
// - o `enviado_por` no payload do n8n, sem tirar nenhum campo, e o token nunca
//   indo ao webhook;
// - sair (ou cair) com o comprovante pendente: nada vai ao webhook depois.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8');

function entre(inicio, fim, { incluiFim = false } = {}) {
  const i = html.indexOf(inicio);
  const f = html.indexOf(fim, i + 1);
  assert.ok(i > 0 && f > i, `o trecho "${inicio.slice(0, 50)}" sumiu do index.html — ajuste os marcadores`);
  return html.slice(i, incluiFim ? f + fim.length : f);
}

const TRECHOS = [
  entre('/* ===== Login central em manutenção (TSK-1238) ===== */', '/* ===== fim do login central em manutenção ===== */', { incluiFim: true }),
  entre('/* ===== Sessão do Lobby (TSK-1513) ===== */', '/* ===== fim da sessão do Lobby ===== */', { incluiFim: true }),
  entre('async function autorizaNegociacao', '/* ============================================================================\n * COMPROVANTE DA VENDA'),
  entre('class ComprovanteRecusado extends Error', '// ===== Máscaras ====='),
  entre('function extractWebhookError(body)', '// ===== Cobrança Pix da entrada ====='),
].join('\n');

const EXPOSTOS = [
  'sessaoLobby', 'SESSAO_CHAVE', 'MSG_SESSAO_EXPIRADA', 'MSG_SESSAO_ENCERRADA', 'MSG_SEM_PAPEL_PARA_AUTORIZAR',
  'SessaoDerrubada', 'SessaoTrocada', 'fimDeSessao', 'sessaoVigente', 'normalizaSessao', 'abreSessaoGuardada',
  'iniciaSessao', 'encerraSessao', 'derrubaSessao', 'motivoDaQueda', 'apiFetch', 'entraNoLobby',
  'confereSessaoAoAbrir', 'confereSessao', 'enviaVenda', 'pedeComprovante', 'postCadastro',
  'ComprovanteRecusado', 'autorizaNegociacao',
];

// ----- o mundo de mentira -----------------------------------------------------

function guardaDeMentira() {
  const mapa = new Map();
  return {
    mapa,
    getItem: (k) => (mapa.has(k) ? mapa.get(k) : null),
    setItem: (k, v) => mapa.set(k, String(v)),
    removeItem: (k) => mapa.delete(k),
  };
}

const resposta = (status, corpo) => ({
  status, ok: status >= 200 && status < 300,
  json: async () => { if (corpo === undefined) throw new Error('sem corpo'); return corpo; },
});

// Um `fetch` que anota cada chamada e responde pela regra de quem testa. A regra
// pode devolver uma Promise, para segurar a resposta (o "pendente").
function fetchDeMentira(regra) {
  const chamadas = [];
  const f = async (url, init = {}) => {
    chamadas.push({ url, init, headers: init.headers || {}, corpo: init.body ? JSON.parse(init.body) : null });
    return regra(url, init);
  };
  f.chamadas = chamadas;
  f.para = (trecho) => chamadas.filter((c) => c.url.includes(trecho));
  return f;
}

const WEBHOOK = 'https://n8n.sejaap.com.br/webhook/lobby-apn';
const daquiAUmaHora = () => Math.floor(Date.now() / 1000) + 3600;
const LOGIN_OK = {
  token: 'eyJ.sessao.da-carla', exp: daquiAUmaHora(), nome: 'Carla Consultora',
  email: 'carla@sejaap.com.br', papel: '', podeAutorizarNegociacao: false,
  podePublicarTabela: false, precisaTrocarSenha: false,
};

function lobby(regra = () => resposta(200, {}), guarda = guardaDeMentira()) {
  const fetch = fetchDeMentira(regra);
  const api = new Function(
    'fetch', 'sessionStorage', 'ERRO_AUTORIZACAO_PADRAO',
    TRECHOS + `\nreturn { ${EXPOSTOS.join(', ')} };`,
  )(fetch, guarda, 'Não foi possível autorizar. Tente de novo.');
  const quedas = [];
  api.sessaoLobby.aoDerrubar = (motivo, caiu) => quedas.push({ motivo, caiu });
  return { ...api, fetch, guarda, quedas };
}

function adiado() {
  let resolve;
  const promessa = new Promise((r) => { resolve = r; });
  return { promessa, resolve };
}

const VALORES = { fluxo: 'apn', categoria_id: 'apn', produto_id: '', negociado: false, valor_total: 37000, entrada: 37000, cronograma: [], protocolo: 'APN-261116012345' };
const payloadDeAntes = () => ({
  origem: 'lobby_conecta_ap', etapa: 'envio_venda_apn', departamento: null, categoria_financeira: null,
  categoria: { id: 'apn', nome: 'APN' }, tipo_pessoa: 'PJ', cliente: { nome: 'Cliente' }, empresa: {},
  contato: {}, pagamento: { valor: 37000 }, origem_venda: 'teste', metadata: { canal: 'lobby_conecta_ap' },
  protocolo: VALORES.protocolo, autorizado_por: { nome: 'Gustavo Gerente', email: 'g@sejaap.com.br', papel: 'gerente' },
});

// ----- entrar -----------------------------------------------------------------

test('entrar: e-mail e senha em /api/sessao, sem Authorization, e devolve o corpo do login', async () => {
  const l = lobby((url) => (url === '/api/sessao' ? resposta(200, LOGIN_OK) : resposta(404, {})));
  const dados = await l.entraNoLobby('carla@sejaap.com.br', 'segredo');
  assert.deepEqual(dados, LOGIN_OK);
  const [chamada] = l.fetch.chamadas;
  assert.equal(chamada.init.method, 'POST');
  assert.deepEqual(chamada.corpo, { email: 'carla@sejaap.com.br', senha: 'segredo' });
  assert.equal(chamada.headers.Authorization, undefined);
});

test('entrar: a senha errada (401) é a frase do servidor e não derruba quem já está logado', async () => {
  const l = lobby(() => resposta(401, { erro: 'E-mail ou senha incorretos.' }));
  l.iniciaSessao(LOGIN_OK);
  await assert.rejects(l.entraNoLobby('carla@sejaap.com.br', 'errada'), { message: 'E-mail ou senha incorretos.' });
  assert.equal(l.quedas.length, 0);
  assert.ok(l.sessaoLobby.atual);
});

test('entrar: o 503 de manutenção vira o aviso, e a rede fora não vira "senha incorreta"', async () => {
  const manutencao = lobby(() => resposta(503, { erro: 'manutencao', mensagem: 'Atualização do sistema.', previsao: null }));
  await assert.rejects(manutencao.entraNoLobby('a@b.c', 'x'), (e) => e.manutencao && e.manutencao.mensagem === 'Atualização do sistema.');
  const semRede = lobby(() => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(semRede.entraNoLobby('a@b.c', 'x'), /Sem conexão para entrar/);
});

// ----- o que fica guardado ----------------------------------------------------

test('a sessão vai para o sessionStorage com seis campos, e só eles', () => {
  const l = lobby();
  l.iniciaSessao(LOGIN_OK);
  const guardada = JSON.parse(l.guarda.getItem(l.SESSAO_CHAVE));
  assert.deepEqual(Object.keys(guardada).sort(), ['email', 'exp', 'nome', 'papel', 'podeAutorizarNegociacao', 'token']);
  assert.deepEqual(l.abreSessaoGuardada(), { sessao: guardada, motivo: null });
  assert.ok(!/localStorage\s*[.[]/.test(html), 'a sessão não pode ir para o localStorage');
});

test('a sessão vencida guardada sai do armazenamento e vira o motivo do login', () => {
  const l = lobby();
  l.guarda.setItem(l.SESSAO_CHAVE, JSON.stringify({ ...LOGIN_OK, exp: Math.floor(Date.now() / 1000) - 1 }));
  assert.deepEqual(l.abreSessaoGuardada(), { sessao: null, motivo: l.MSG_SESSAO_EXPIRADA });
  assert.equal(l.guarda.getItem(l.SESSAO_CHAVE), null);
});

test('lixo no armazenamento é como não ter sessão, sem motivo', () => {
  const l = lobby();
  l.guarda.setItem(l.SESSAO_CHAVE, '{não é json');
  assert.deepEqual(l.abreSessaoGuardada(), { sessao: null, motivo: null });
  assert.deepEqual(lobby().abreSessaoGuardada(), { sessao: null, motivo: null });
});

// ----- o 401 ------------------------------------------------------------------

test('apiFetch põe o Authorization da sessão', async () => {
  const l = lobby(() => resposta(200, {}));
  l.iniciaSessao(LOGIN_OK);
  await l.apiFetch('/api/catalogo', { cache: 'no-store' });
  const [chamada] = l.fetch.chamadas;
  assert.equal(chamada.headers.Authorization, `Bearer ${LOGIN_OK.token}`);
  assert.equal(chamada.init.cache, 'no-store');
});

test('401 derruba a sessão e volta ao login com a frase do servidor', async () => {
  const l = lobby(() => resposta(401, { erro: 'Sessão encerrada.' }));
  const sessao = l.iniciaSessao(LOGIN_OK);
  await assert.rejects(l.apiFetch('/api/catalogo'), (e) => e.sessaoDerrubada && e.message === 'Sessão encerrada.');
  assert.deepEqual(l.quedas, [{ motivo: 'Sessão encerrada.', caiu: sessao }]);
  assert.equal(l.sessaoLobby.atual, null);
  assert.equal(l.guarda.getItem(l.SESSAO_CHAVE), null);
});

test('passou do exp: "Sua sessão expirou", sem nem chamar a API', async () => {
  const l = lobby(() => resposta(200, {}));
  l.iniciaSessao({ ...LOGIN_OK, exp: Math.floor(Date.now() / 1000) - 5 });
  await assert.rejects(l.apiFetch('/api/catalogo'), (e) => e.sessaoDerrubada && e.message === l.MSG_SESSAO_EXPIRADA);
  assert.equal(l.fetch.chamadas.length, 0);
  assert.equal(l.quedas[0].motivo, l.MSG_SESSAO_EXPIRADA);
  // E o 401 que chega depois do exp diz "expirou", e não a frase do servidor.
  assert.equal(l.motivoDaQueda({ exp: 100 }, { erro: 'O token é inválido ou expirado' }, 100 * 1000), l.MSG_SESSAO_EXPIRADA);
  assert.equal(l.motivoDaQueda({ exp: 200 }, {}, 100 * 1000), l.MSG_SESSAO_ENCERRADA);
});

test('rede fora e 5xx não derrubam a sessão', async () => {
  const fora = lobby(() => { throw new TypeError('Failed to fetch'); });
  fora.iniciaSessao(LOGIN_OK);
  await assert.rejects(fora.apiFetch('/api/catalogo'), TypeError);
  const erro = lobby(() => resposta(502, undefined));
  erro.iniciaSessao(LOGIN_OK);
  assert.equal((await erro.apiFetch('/api/catalogo')).status, 502);
  for (const l of [fora, erro]) {
    assert.equal(l.quedas.length, 0);
    assert.ok(l.sessaoLobby.atual);
  }
});

test('sem sessão, apiFetch não chama nada', async () => {
  const l = lobby();
  await assert.rejects(l.apiFetch('/api/catalogo'), (e) => e.sessaoTrocada);
  assert.equal(l.fetch.chamadas.length, 0);
});

test('antes do CNPJ, CEP e Pix (webhooks diretos): só o 401 barra', async () => {
  const caiu = lobby(() => resposta(401, { erro: 'Sessão encerrada.' }));
  caiu.iniciaSessao(LOGIN_OK);
  await assert.rejects(caiu.confereSessao(), (e) => e.sessaoDerrubada);
  for (const regra of [() => resposta(200, {}), () => resposta(503, {}), () => { throw new TypeError('rede'); }]) {
    const l = lobby(regra);
    l.iniciaSessao(LOGIN_OK);
    await l.confereSessao();
    assert.equal(l.quedas.length, 0);
  }
  // A ordem, no index.html: a conferência vem antes do webhook.
  for (const [funcao, webhook] of [['const validateCnpj', 'fetchCnpj(s.cnpj)'], ['const validateCep', 'fetchCep(s.cep)'], ['const generatePix', 'fetch(PIX_CREATE_URL']]) {
    const corpo = entre(funcao, webhook, { incluiFim: true });
    assert.ok(corpo.includes('await confereSessao()'), `${funcao} não confere a sessão antes do webhook`);
  }
});

test('ao abrir, a sessão guardada é conferida e traz o papel de hoje', async () => {
  const l = lobby((url) => (url === '/api/sessao/atual'
    ? resposta(200, { nome: 'Carla C.', email: LOGIN_OK.email, papel: 'gerente', podeAutorizarNegociacao: true })
    : resposta(404, {})));
  l.iniciaSessao(LOGIN_OK);
  const conferida = await l.confereSessaoAoAbrir();
  assert.equal(conferida.papel, 'gerente');
  assert.equal(conferida.podeAutorizarNegociacao, true);
  assert.equal(conferida.token, LOGIN_OK.token);
  assert.equal(JSON.parse(l.guarda.getItem(l.SESSAO_CHAVE)).papel, 'gerente');
});

// ----- o comprovante e o envio ------------------------------------------------

function regraDaVenda({ comprovante = () => resposta(200, { comprovante: 'id.assinatura', expira_em: 'x' }), webhook = () => resposta(200, { ok: true }) } = {}) {
  return (url, init) => {
    if (url === '/api/venda/comprovante') return comprovante(url, init);
    if (url.startsWith('https://n8n.sejaap.com.br/')) return webhook(url, init);
    return resposta(404, {});
  };
}

test('comprovante: sessão no Authorization, quem autoriza no X-Lobby-Autorizacao', async () => {
  const l = lobby(regraDaVenda());
  l.iniciaSessao(LOGIN_OK);
  await l.pedeComprovante(VALORES, 'eyJ.token.do-gerente');
  const [chamada] = l.fetch.para('/api/venda/comprovante');
  assert.equal(chamada.headers.Authorization, `Bearer ${LOGIN_OK.token}`);
  assert.equal(chamada.headers['X-Lobby-Autorizacao'], 'Bearer eyJ.token.do-gerente');
  assert.deepEqual(chamada.corpo, VALORES);

  await l.pedeComprovante(VALORES);
  assert.equal(l.fetch.para('/api/venda/comprovante')[1].headers['X-Lobby-Autorizacao'], undefined);
});

test('enviado_por vai ao n8n com nome e e-mail, sem tirar campo nenhum, e o token não vai', async () => {
  const l = lobby(regraDaVenda());
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  const antes = payloadDeAntes();
  await l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, tokenDeQuemAutoriza: 'eyJ.gerente', geracao });
  const [envio] = l.fetch.para('n8n.sejaap.com.br');
  assert.deepEqual(envio.corpo.enviado_por, { nome: 'Carla Consultora', email: 'carla@sejaap.com.br' });
  for (const [chave, valor] of Object.entries(antes)) assert.deepEqual(envio.corpo[chave], valor, chave);
  assert.equal(envio.corpo.comprovante, 'id.assinatura');
  assert.deepEqual(envio.corpo.comprovante_valores, VALORES);
  assert.equal(envio.corpo.comprovante_erro, null);
  assert.deepEqual(
    Object.keys(envio.corpo).sort(),
    [...Object.keys(antes), 'enviado_por', 'comprovante', 'comprovante_valores', 'comprovante_erro'].sort(),
  );
  assert.ok(!JSON.stringify(envio).includes(LOGIN_OK.token), 'o token da sessão não vai ao webhook');
  assert.equal(envio.headers.Authorization, undefined);
});

test('os quatro fluxos passam pelo enviaVenda, e só ele chama o postCadastro', () => {
  for (const url of ['SEND_WEBHOOK_URL', 'APN_SEND_WEBHOOK_URL', 'DH_SEND_WEBHOOK_URL', 'DH_AVALIACAO_SEND_WEBHOOK_URL']) {
    assert.match(html, new RegExp(`enviaVenda\\(\\{\\s*url: ${url},`), url);
  }
  assert.equal(html.match(/postCadastro\(/g).length, 2, 'a definição e a chamada no enviaVenda');
  assert.equal(html.match(/pedeComprovante\(/g).length, 2, 'a definição e a chamada no enviaVenda');
});

test('o 401 do comprovante PARA a venda: nada vai ao n8n, e a tela volta ao login', async () => {
  const l = lobby(regraDaVenda({ comprovante: () => resposta(401, { erro: 'Sessão encerrada.' }) }));
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  await assert.rejects(
    l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, geracao }),
    (e) => e.sessaoDerrubada,
  );
  assert.equal(l.fetch.para('n8n.sejaap.com.br').length, 0);
  assert.equal(l.quedas.length, 1);
});

test('rede fora no comprovante segue a regra degradada de sempre', async () => {
  for (const [comprovante, erro] of [
    [() => { throw new TypeError('rede'); }, 'servidor_indisponivel'],
    [() => resposta(502, undefined), 'http_502'],
  ]) {
    const l = lobby(regraDaVenda({ comprovante }));
    const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
    await l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, geracao });
    const [envio] = l.fetch.para('n8n.sejaap.com.br');
    assert.equal(envio.corpo.comprovante, null);
    assert.equal(envio.corpo.comprovante_erro, erro);
    assert.ok(envio.corpo.enviado_por);
    assert.equal(l.quedas.length, 0);
  }
});

test('a recusa (422) para a venda, como antes', async () => {
  const l = lobby(regraDaVenda({ comprovante: () => resposta(422, { erro: 'O valor não confere com a tabela.' }) }));
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  await assert.rejects(
    l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, geracao }),
    (e) => e instanceof l.ComprovanteRecusado && e.message === 'O valor não confere com a tabela.',
  );
  assert.equal(l.fetch.para('n8n.sejaap.com.br').length, 0);
});

// ----- sair (ou cair) com coisa a caminho -----------------------------------

test('sair com o comprovante pendente: ele volta e nada vai ao webhook', async () => {
  const pendente = adiado();
  const l = lobby(regraDaVenda({ comprovante: () => pendente.promessa }));
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  const envio = l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, geracao });
  await new Promise((r) => setImmediate(r));
  assert.equal(l.fetch.para('/api/venda/comprovante').length, 1, 'o comprovante estava a caminho');

  l.encerraSessao();   // o "Sair"
  pendente.resolve(resposta(200, { comprovante: 'id.assinatura', expira_em: 'x' }));

  await assert.rejects(envio, (e) => e.sessaoTrocada);
  assert.equal(l.fetch.para('n8n.sejaap.com.br').length, 0);
  assert.equal(l.guarda.getItem(l.SESSAO_CHAVE), null);
});

test('sair e entrar outra pessoa antes de o comprovante voltar: a venda velha não sai no nome dela', async () => {
  const pendente = adiado();
  const l = lobby(regraDaVenda({ comprovante: () => pendente.promessa }));
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  const envio = l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, geracao });
  await new Promise((r) => setImmediate(r));
  l.encerraSessao();
  l.iniciaSessao({ ...LOGIN_OK, token: 'eyJ.outra', nome: 'Outra Pessoa', email: 'outra@sejaap.com.br' });
  pendente.resolve(resposta(200, { comprovante: 'id.assinatura', expira_em: 'x' }));
  await assert.rejects(envio, (e) => e.sessaoTrocada);
  assert.equal(l.fetch.para('n8n.sejaap.com.br').length, 0);
});

test('a sessão que cai com o comprovante pendente: idem', async () => {
  const pendente = adiado();
  const l = lobby((url) => (url === '/api/venda/comprovante' ? pendente.promessa
    : url === '/api/sessao/atual' ? resposta(401, { erro: 'Sessão encerrada.' }) : resposta(200, {})));
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  const envio = l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, geracao });
  await new Promise((r) => setImmediate(r));
  await assert.rejects(l.confereSessao(), (e) => e.sessaoDerrubada);   // outra chamada levou o 401
  pendente.resolve(resposta(200, { comprovante: 'id.assinatura', expira_em: 'x' }));
  await assert.rejects(envio, (e) => e.sessaoTrocada);
  assert.equal(l.fetch.para('n8n.sejaap.com.br').length, 0);
  assert.equal(l.quedas.length, 1, 'uma queda só');
});

test('a resposta do webhook que chega depois de sair não volta à tela', async () => {
  const pendente = adiado();
  const l = lobby(regraDaVenda({ webhook: () => pendente.promessa }));
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  const envio = l.enviaVenda({ url: WEBHOOK, payload: payloadDeAntes(), valores: VALORES, geracao });
  for (let i = 0; i < 5 && !l.fetch.para('n8n.sejaap.com.br').length; i++) await new Promise((r) => setImmediate(r));
  assert.equal(l.fetch.para('n8n.sejaap.com.br').length, 1, 'a venda já tinha saído');
  l.encerraSessao();
  pendente.resolve(resposta(200, { ok: true }));
  await assert.rejects(envio, (e) => e.sessaoTrocada);
});

test('venda finalizada numa sessão não sai na seguinte', async () => {
  const l = lobby(regraDaVenda());
  const geracao = l.iniciaSessao(LOGIN_OK) && l.sessaoLobby.geracao;
  l.encerraSessao();
  l.iniciaSessao(LOGIN_OK);
  await assert.rejects(l.postCadastro(WEBHOOK, payloadDeAntes(), geracao), (e) => e.sessaoTrocada);
  assert.equal(l.fetch.para('n8n.sejaap.com.br').length, 0);
});

// ----- o modal do gerente ----------------------------------------------------

test('o modal do gerente pede "para: negociacao" e confere o papel', async () => {
  const semPapel = lobby(() => resposta(200, { ...LOGIN_OK, podeAutorizarNegociacao: false }));
  await assert.rejects(semPapel.autorizaNegociacao('carla@sejaap.com.br', 'x'), { message: semPapel.MSG_SEM_PAPEL_PARA_AUTORIZAR });
  assert.deepEqual(semPapel.fetch.chamadas[0].corpo, { email: 'carla@sejaap.com.br', senha: 'x', para: 'negociacao' });

  const gerente = lobby(() => resposta(200, { ...LOGIN_OK, papel: 'gerente', podeAutorizarNegociacao: true }));
  assert.equal((await gerente.autorizaNegociacao('g@sejaap.com.br', 'x')).papel, 'gerente');
});

test('a senha errada do gerente no modal não derruba quem está vendendo', async () => {
  const l = lobby(() => resposta(401, { erro: 'E-mail ou senha incorretos.' }));
  l.iniciaSessao(LOGIN_OK);
  await assert.rejects(l.autorizaNegociacao('g@sejaap.com.br', 'errada'), { message: 'E-mail ou senha incorretos.' });
  assert.equal(l.quedas.length, 0);
  assert.ok(l.sessaoLobby.atual);
});

// ----- a tela ----------------------------------------------------------------

test('a tela de login tem o link do esqueci a senha, o aviso de manutenção e o de trocar a senha', () => {
  const tela = entre('function TelaDeLogin(', 'function TelaAbrindo(');
  assert.ok(tela.includes('href="/esqueci-senha"'));
  assert.ok(tela.includes('entraNoLobby('));
  assert.ok(tela.includes('Temporariamente indisponível') && tela.includes('Em manutenção'));
  assert.ok(tela.includes('dados.precisaTrocarSenha'));
  const barra = entre('function BarraDoTopo(', '/* ===== Contagem regressiva');
  assert.ok(barra.includes('className="who"') && barra.includes('Sair'));
});

test('sem sessão, o App não mostra catálogo nem formulário', () => {
  const app = entre('function App({ motivoAoAbrir })', 'function carregaCatalogo');
  const render = app.slice(app.indexOf('/* ======================= RENDER ======================= */'));
  const login = render.indexOf('if (!sessao) return <TelaDeLogin');
  assert.ok(login > 0, 'o login tem de ser o primeiro retorno do render');
  assert.ok(login < render.indexOf('renderLobby()') && login < render.indexOf('<div className="app-shell">'));
});
