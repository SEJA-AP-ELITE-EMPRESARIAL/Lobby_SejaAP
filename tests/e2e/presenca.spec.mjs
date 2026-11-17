/**
 * Ponta a ponta do sinal de presença (TSK-1520): o index.html de verdade no
 * Chromium, o backend local e o Conecta falso (`apoio/conecta_falso.py`). Rodar
 * com `rodar-presenca.sh`, que sobe os três.
 *
 * O passo 3 do "Como confirmo" depende do Conecta em produção; aqui, contra o
 * falso:
 * - logado, a pessoa aparece no sistema Lobby com a tela certa em até 15 s, e
 *   de novo a cada troca de tela (a troca dupla manda um sinal só);
 * - um sinal por minuto, e um na volta à aba;
 * - na tela de login e nas de senha, nenhum pedido de sinal sai do navegador;
 * - o erro do sinal (Conecta fora, 500, 401) nunca aparece, não repete em laço
 *   e não tira ninguém do lobby; sair cala o sinal.
 *
 * Os prints vão para tests/e2e/prints-da-rodada/ (fora do git), ou para
 * `E2E_PRINTS`; os versionados, de referência, estão em tests/e2e/prints/.
 */
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

const CONECTA = process.env.E2E_CONECTA;
// Por padrão numa pasta fora do git: cada rodada muda a hora no painel e
// reescreveria os PNG versionados. Para renovar os de tests/e2e/prints/:
// `E2E_PRINTS=tests/e2e/prints tests/e2e/rodar-presenca.sh`.
const PRINTS = `${resolve(process.env.E2E_PRINTS || fileURLToPath(new URL('./prints-da-rodada/', import.meta.url)))}/`;
const ROTA = '/api/presenca';
const EMAIL = 'carla@e2e.sejaap.com.br';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

mkdirSync(PRINTS, { recursive: true });

async function sinais(request) {
  return (await request.get(`${CONECTA}/_sinais`)).json();
}

async function limparSinais(request) {
  await request.post(`${CONECTA}/_sinais/limpar`);
}

/** Espera o Conecta falso receber um sinal com este módulo e esta tela; devolve o sinal. */
async function esperarSinal(request, { modulo, tela }, { prazo = 15 * 1000 } = {}) {
  let achado;
  await expect
    .poll(
      async () => {
        achado = (await sinais(request)).find((s) => s.corpo.modulo === modulo && s.corpo.tela === tela);
        return Boolean(achado);
      },
      { timeout: prazo, intervals: [100, 250, 500], message: `sinal ${modulo} · ${tela}` },
    )
    .toBe(true);
  return achado;
}

/** Todo pedido de sinal que sair desta página (e a resposta de cada um). */
function vigiarSinais(page) {
  const saidos = [];
  page.on('request', (pedido) => {
    if (new URL(pedido.url()).pathname === ROTA) saidos.push(pedido);
  });
  return saidos;
}

async function entrar(page) {
  await page.goto('/');
  await page.getByLabel('E-mail').fill(EMAIL);
  await page.getByLabel('Senha').fill('senha-do-e2e');
  await page.locator('form button[type=submit]').click();
  await expect(page.locator('button.cat-card', { hasText: 'APN' })).toBeVisible({ timeout: 30 * 1000 });
}

const categoria = (page, nome) => page.locator('button.cat-card', { hasText: nome });

async function printDoPainel(context, arquivo) {
  const painel = await context.newPage();
  await painel.goto(`${CONECTA}/_painel`);
  await painel.screenshot({ path: PRINTS + arquivo, fullPage: true });
  await painel.close();
}

/** Diz à página que a aba voltou a ficar visível (headless não troca de aba). */
async function voltarAAba(page) {
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
}

// O Conecta falso guarda os sinais entre um teste e outro: sem limpar, o
// `esperarSinal` acharia o do teste anterior. A espera antes de limpar deixa
// chegar o sinal que o teste anterior mandou no fim (o backend ainda o estava
// repassando quando a página fechou).
test.beforeEach(async ({ request }) => {
  await request.post(`${CONECTA}/_voltar`);
  await new Promise((r) => setTimeout(r, 1500));
  await limparSinais(request);
});

test('passo 3: logado, o sinal chega com a tela certa ao abrir e a cada troca de tela', async ({ page, context, request }) => {
  const saidos = vigiarSinais(page);

  // A tela de login, aberta e parada: nada.
  await page.goto('/');
  await expect(page.getByLabel('E-mail')).toBeVisible({ timeout: 30 * 1000 });
  await page.waitForTimeout(2000);
  expect(saidos).toHaveLength(0);
  await page.screenshot({ path: PRINTS + '01-login-sem-sinal.png' });

  await page.getByLabel('E-mail').fill(EMAIL);
  await page.getByLabel('Senha').fill('senha-do-e2e');
  const entrou = Date.now();
  await page.locator('form button[type=submit]').click();
  await expect(categoria(page, 'APN')).toBeVisible({ timeout: 30 * 1000 });

  const lobby = await esperarSinal(request, { modulo: 'Lobby', tela: 'Categorias' });
  expect(Date.now() - entrou).toBeLessThan(15 * 1000);
  expect(lobby).toMatchObject({ sistema: 'Lobby', com_chave: true, pessoa: EMAIL });
  expect(lobby.corpo.identidade_id).toMatch(UUID);
  // A última interação chega com o fuso, e é o clique em Entrar (ou depois).
  expect(lobby.corpo.ultima_interacao).toMatch(/([+-]\d\d:\d\d|Z)$/);
  expect(Date.parse(lobby.corpo.ultima_interacao)).toBeGreaterThanOrEqual(entrou - 1000);
  expect(Date.parse(lobby.corpo.ultima_interacao)).toBeLessThanOrEqual(Date.now());
  // O pedido sai para o backend do Lobby, com a sessão, e não para o Conecta.
  expect(saidos[0].method()).toBe('POST');
  expect(new URL(saidos[0].url()).origin).toBe(new URL(page.url()).origin);
  expect(saidos[0].headers().authorization).toMatch(/^Bearer ey/);
  expect((await saidos[0].response()).status()).toBe(204);
  // Entrar passa pelo "Abrindo o lobby…": sai um sinal só, o da grade.
  await page.waitForTimeout(1500);
  expect((await sinais(request)).map((s) => s.corpo.tela)).toEqual(['Categorias']);
  await page.screenshot({ path: PRINTS + '02-lobby-logado.png' });
  await printDoPainel(context, '03-painel-falso-lobby.png');

  // Troca de tela: a APN. Um sinal na hora, com o clique como última interação.
  const antesDoClique = Date.now();
  await categoria(page, 'APN').click();
  const apn = await esperarSinal(request, { modulo: 'APN', tela: 'Cliente' }, { prazo: 5000 });
  expect(Date.parse(apn.corpo.ultima_interacao)).toBeGreaterThanOrEqual(antesDoClique - 1000);
  await page.screenshot({ path: PRINTS + '04-apn-cliente.png' });
  await printDoPainel(context, '05-painel-falso-apn.png');

  // Duas trocas seguidas (voltar ao lobby e abrir Produtos de imediato), como
  // num redirecionamento: sai um sinal só, o da tela onde a pessoa ficou.
  // Dentro da página, num quadro só: o clique do Playwright espera a animação
  // da grade parar, e isso passaria da espera da troca.
  await page.waitForTimeout(1000);
  await limparSinais(request);
  await page.evaluate(async () => {
    const botao = (seletor, texto) => [...document.querySelectorAll(seletor)].find((b) => b.textContent.includes(texto));
    botao('button', 'Voltar ao lobby').click();
    await new Promise((r) => requestAnimationFrame(r));
    botao('button.cat-card', 'Produtos').click();
  });
  await esperarSinal(request, { modulo: 'Produtos', tela: 'Produto' }, { prazo: 5000 });
  await page.waitForTimeout(2000);
  expect((await sinais(request)).map((s) => `${s.corpo.modulo} · ${s.corpo.tela}`)).toEqual(['Produtos · Produto']);

  // Escolher o produto também é trocar de tela: o produto vai junto.
  await page.getByText('Recrutamento e Seleção', { exact: true }).first().click();
  await esperarSinal(request, { modulo: 'Produtos', tela: 'Produto · Recrutamento e Seleção' }, { prazo: 5000 });
  await page.screenshot({ path: PRINTS + '06-produtos-recrutamento.png' });

  // E o Elite, que começa pela empresa.
  await page.getByRole('button', { name: /Voltar ao lobby/ }).click();
  await categoria(page, 'Elite').click();
  await esperarSinal(request, { modulo: 'Elite', tela: 'Empresa' }, { prazo: 5000 });
  await printDoPainel(context, '07-painel-falso-elite.png');
});

test('um sinal por minuto com a aba aberta, e um na volta à aba', async ({ page, request }) => {
  // O relógio da página na mão: o minuto passa sem o teste esperar um minuto.
  await page.clock.install();
  await entrar(page);
  await esperarSinal(request, { modulo: 'Lobby', tela: 'Categorias' });
  await limparSinais(request);
  // Conta o que sai da página, na hora: o que chega ao Conecta falso ainda
  // poderia estar no caminho quando o teste olha.
  const saidos = vigiarSinais(page);

  // A folga cobre o tempo de verdade que corre entre um passo e outro.
  await page.clock.runFor(50 * 1000);
  expect(saidos).toHaveLength(0);
  await page.clock.runFor(15 * 1000);
  await esperarSinal(request, { modulo: 'Lobby', tela: 'Categorias' }, { prazo: 5000 });
  expect(saidos).toHaveLength(1);

  // Minuto a minuto, esperando cada sinal chegar: num salto de 2 minutos, o
  // corte de 10 s do pedido (`PRESENCA_TEMPO_MAXIMO_MS`) venceria no relógio de
  // mentira com o pedido de verdade ainda a caminho, e o abortaria.
  for (const total of [2, 3]) {
    await page.clock.runFor(60 * 1000);
    await expect.poll(async () => (await sinais(request)).length, { timeout: 5000 }).toBe(total);
  }
  expect(saidos).toHaveLength(3);

  // A volta à aba, 40 s depois do último: na hora.
  await page.clock.runFor(40 * 1000);
  expect(saidos).toHaveLength(3);
  await voltarAAba(page);
  await expect.poll(() => saidos.length, { timeout: 5000 }).toBe(4);
});

test('sem login, nenhum sinal: a tela de login e as de senha, abertas por 3 minutos', async ({ page, context, request }) => {
  await page.clock.install();
  const saidos = vigiarSinais(page);

  for (const rota of ['/', '/esqueci-senha', '/definir-senha']) {
    await page.goto(rota);
    await page.waitForLoadState('load');
    await page.waitForTimeout(1500);   // o Babel transpila o index.html no navegador
    // Três minutos de tela aberta, com a pessoa mexendo: logado seriam 3 sinais.
    await page.mouse.click(10, 10);
    await page.keyboard.press('Tab');
    await page.clock.runFor(3 * 60 * 1000);
    await voltarAAba(page);
    if (rota === '/') {
      await expect(page.getByLabel('E-mail')).toBeVisible();
      await page.screenshot({ path: PRINTS + '08-login-3-minutos-sem-sinal.png' });
    }
  }

  expect(saidos.map((p) => p.url())).toEqual([]);
  expect(await sinais(request)).toEqual([]);
  expect(await page.evaluate(() => sessionStorage.getItem('lobby.sessao'))).toBeNull();
  await printDoPainel(context, '09-painel-falso-sem-login-sem-sinal.png');
});

async function continuaNoLobby(page) {
  await expect(categoria(page, 'APN')).toBeVisible();
  await expect(page.getByLabel('E-mail')).toHaveCount(0);
}

test('o erro do sinal nunca aparece, não repete e não tira ninguém do lobby; sair cala', async ({ page, request }) => {
  await page.clock.install();
  await entrar(page);
  await esperarSinal(request, { modulo: 'Lobby', tela: 'Categorias' });
  const saidos = vigiarSinais(page);

  // O backend com erro (500): um pedido por minuto, sem repetir em laço.
  // Minuto a minuto, pelo mesmo motivo do teste do relógio (o corte de 10 s).
  await page.route(`**${ROTA}`, (rota) => rota.fulfill({ status: 500, body: '{"erro": "x"}', contentType: 'application/json' }));
  for (const total of [1, 2, 3]) {
    await page.clock.runFor(60 * 1000);
    await expect.poll(() => saidos.length, { timeout: 5000 }).toBe(total);
  }
  await page.waitForTimeout(500);
  expect(saidos).toHaveLength(3);
  await continuaNoLobby(page);

  // O 401 do sinal: a sessão fica, a tela fica, e o mesmo token não manda mais.
  await page.unroute(`**${ROTA}`);
  await page.route(`**${ROTA}`, (rota) => rota.fulfill({ status: 401, body: '{"erro": "Sessão encerrada."}', contentType: 'application/json' }));
  await page.clock.runFor(60 * 1000);
  await expect.poll(() => saidos.length, { timeout: 5000 }).toBe(4);
  await page.clock.runFor(3 * 60 * 1000);
  await voltarAAba(page);
  await page.waitForTimeout(500);
  expect(saidos).toHaveLength(4);
  await continuaNoLobby(page);
  expect(await page.evaluate(() => sessionStorage.getItem('lobby.sessao'))).not.toBeNull();
  await page.screenshot({ path: PRINTS + '10-erro-do-sinal-nao-aparece.png' });
  await page.unroute(`**${ROTA}`);

  // Sair: a tela de login, e o sinal cala.
  await page.locator('button.topbtn', { hasText: 'Sair' }).click();
  await expect(page.getByLabel('E-mail')).toBeVisible();
  await page.clock.runFor(3 * 60 * 1000);
  await voltarAAba(page);
  await page.waitForTimeout(500);
  expect(saidos).toHaveLength(4);
});

// POR ÚLTIMO, de propósito: a falha do Conecta pausa o `enviar_presenca` do
// backend por 60 s de relógio de verdade (é do processo, no cliente 1.6.0), e um
// teste depois deste não veria sinal nenhum chegar. Teste novo entra ACIMA.
test('por último: com o Conecta fora, o backend responde 204 e a tela nem percebe', async ({ page, request }) => {
  await page.clock.install();
  await entrar(page);
  await esperarSinal(request, { modulo: 'Lobby', tela: 'Categorias' });
  await limparSinais(request);
  const respostas = [];
  page.on('response', (r) => { if (new URL(r.url()).pathname === ROTA) respostas.push(r.status()); });

  await request.post(`${CONECTA}/_falhar`);
  await page.clock.runFor(60 * 1000);
  await expect.poll(() => respostas.length, { timeout: 5000 }).toBe(1);
  expect(respostas).toEqual([204]);
  // E o próximo minuto também (agora com o cliente em pausa, sem nem chamar).
  await page.clock.runFor(60 * 1000);
  await expect.poll(() => respostas.length, { timeout: 5000 }).toBe(2);
  expect(respostas).toEqual([204, 204]);
  await continuaNoLobby(page);
  expect(await sinais(request)).toEqual([]);
});
