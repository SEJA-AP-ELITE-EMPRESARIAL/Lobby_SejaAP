/**
 * Ponta a ponta do Casais de Alta Performance (TSK-1622): o index.html de verdade
 * no Chromium, o backend local e o Conecta falso (`apoio/conecta_falso.py`).
 * Rodar com `rodar-e2e.sh casais.spec.mjs`, que sobe os três.
 *
 * Os webhooks do n8n (CNPJ, CEP e o da venda, onboarding-cliente-casais) são
 * respondidos pelo próprio teste, no navegador (`page.route`): nada sai para o
 * n8n de verdade, e o POST da venda fica aqui para ser conferido. O papel do n8n
 * na validação é o teste chamando o `/api/venda/validar` do backend local.
 *
 * O "Como confirmo" da tarefa, passo a passo:
 * 1. Produtos mostra o Casais, e ele abre Produto, Contratante, Casal, Pagamento,
 *    Resumo;
 * 2. PJ que o Conecta diz ser ativa → R$ 35.000,00, sem controle para trocar; não
 *    ativa → R$ 39.997,00 (e o Conecta fora → cheio, com o aviso);
 * 3. PF: marcar e desmarcar "paga o valor do Elite Empresarial" alterna os dois;
 * 4. sem e-mail, telefone ou um participante, ou o mesmo CPF nos dois: a etapa
 *    não avança e diz o que falta;
 * 5. o POST ao webhook: protocolo CAP-…, contratante, participantes, valor,
 *    tabela, pagamento e as chaves do comprovante; e o número do contrato na tela
 *    de sucesso;
 * 6. o /api/venda/validar: a venda de verdade passa; os R$ 35.000,00 numa PJ não
 *    ativa e um valor fora dos dois, não.
 *
 * Nomes, CPFs e CNPJs são inventados (de dígito válido). Os prints vão para
 * tests/e2e/prints-da-rodada/ (fora do git), ou para `E2E_PRINTS`.
 */
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

const CONECTA = process.env.E2E_CONECTA;
const RAIZ = fileURLToPath(new URL('../../', import.meta.url));
const PRINTS = `${resolve(RAIZ, process.env.E2E_PRINTS || 'tests/e2e/prints-da-rodada')}/`;
const EMAIL = 'carla@e2e.sejaap.com.br';
// O `LOBBY_N8N_TOKEN` do `apoio/settings_e2e.py`.
const N8N = 'token-do-n8n-do-e2e';

const ATIVO = '22.122.190/0001-52';
const INATIVO = '37.598.418/0001-52';
const FORA_DO_AR = '63.580.153/0001-75';
const EMPRESA = {
  razao_social: 'Quintal Azul Comércio de Plantas Ltda',
  logradouro: 'Rua das Acácias', numero: '210', complemento: 'Sala 3',
  bairro: 'Centro', municipio: 'Florianópolis', uf: 'SC', cep: '88010400',
};
const CASAL = [
  { nome: 'Helena Duarte Vasconcelos', cpf: '294.199.881-04' },
  { nome: 'Otávio Brandão Lacerda', cpf: '450.490.714-37' },
];
const PF = { nome: 'Marina Quintela Rocha', cpf: '775.927.214-65' };
const VENCIMENTO = '2026-12-10';

mkdirSync(PRINTS, { recursive: true });

/**
 * O n8n de mentira, no navegador: CNPJ e CEP respondem os dados inventados, e o
 * webhook do Casais guarda o POST e responde como o n8n. Qualquer outro endereço
 * do n8n é abortado — nada vai ao de verdade.
 */
async function n8nFalso(page) {
  const vendas = [];
  const outros = [];
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'content-type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
  };
  const json = (route, corpo) => route.fulfill({
    status: 200, headers: cors, contentType: 'application/json', body: JSON.stringify(corpo),
  });
  await page.route('https://n8n.sejaap.com.br/**', async (route) => {
    const pedido = route.request();
    const caminho = new URL(pedido.url()).pathname;
    if (pedido.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
    if (caminho === '/webhook/brasilapi-cnpj') return json(route, EMPRESA);
    if (caminho === '/webhook/busca-cep') {
      return json(route, { logradouro: 'Rua das Acácias', bairro: 'Centro', localidade: 'Florianópolis', uf: 'SC', cep: '88010-400' });
    }
    if (caminho === '/webhook/onboarding-cliente-casais') {
      vendas.push(pedido.postDataJSON());
      return json(route, { received: true });
    }
    outros.push(pedido.url());
    return route.abort();
  });
  return { vendas, outros };
}

// O card da categoria pelo nome exato: "Produtos" também está em "6 produtos".
const categoria = (page, nome) => page.locator('button.cat-card').filter({ has: page.getByText(nome, { exact: true }) });

async function entrar(page) {
  await page.goto('/');
  await page.getByLabel('E-mail').fill(EMAIL);
  await page.getByLabel('Senha').fill('senha-do-e2e');
  await page.locator('form button[type=submit]').click();
  await expect(categoria(page, 'Produtos')).toBeVisible({ timeout: 30 * 1000 });
}

const continuar = (page) => page.getByRole('button', { name: /Continuar/ }).click();
const faltas = (page) => page.locator('[data-faltas] li');
const etapa = (page, titulo) => expect(page.getByRole('heading', { name: titulo, exact: true })).toBeVisible();

async function abrirCasais(page) {
  await categoria(page, 'Produtos').click();
  await page.locator('button.prod-row', { hasText: 'Casais de Alta Performance' }).click();
  await continuar(page);
  await etapa(page, 'Dados do contratante');
}

async function contratantePJ(page, cnpj) {
  await page.locator('#cas-cnpj').fill(cnpj);
  await page.getByRole('button', { name: /Validar CNPJ/ }).click();
  await expect(page.locator('#cas-razao')).toHaveValue(EMPRESA.razao_social);
}

async function contato(page) {
  await page.locator('#cas-email').fill('financeiro@quintalazul.com.br');
  await page.locator('#cas-telefone').fill('48998761234');
}

async function casal(page, segundoCpf = CASAL[1].cpf) {
  await etapa(page, 'Casal');
  await page.locator('#cas-p1-nome').fill(CASAL[0].nome);
  await page.locator('#cas-p1-cpf').fill(CASAL[0].cpf);
  await page.locator('#cas-p2-nome').fill(CASAL[1].nome);
  await page.locator('#cas-p2-cpf').fill(segundoCpf);
}

async function pagamento(page, { forma = 'Cartão de crédito', parcelas = '12' } = {}) {
  await page.getByRole('button', { name: new RegExp(forma) }).click();
  if (forma === 'Cartão de crédito') await page.locator('#cas-parcelas').selectOption(parcelas);
  await page.locator('#cas-data').fill(VENCIMENTO);
}

/** O papel do n8n: confere o comprovante da venda no backend do Lobby. */
async function validar(request, venda, valores = venda.comprovante_valores) {
  const r = await request.post('/api/venda/validar', {
    headers: { Authorization: `Bearer ${N8N}` },
    data: { comprovante: venda.comprovante, valores },
  });
  expect(r.status()).toBe(200);
  return r.json();
}

async function perguntasAoConecta(request) {
  return (await request.get(`${CONECTA}/_clientes`)).json();
}

test.beforeEach(async ({ request }) => {
  await request.post(`${CONECTA}/_clientes/limpar`);
});

test('PJ ativa no Conecta: R$ 35.000,00 travado, o que falta em cada etapa, o envio e a validação', async ({ page, request }) => {
  const n8n = await n8nFalso(page);
  await entrar(page);

  // 1. O card em Produtos, e as cinco etapas.
  await categoria(page, 'Produtos').click();
  const card = page.locator('button.prod-row', { hasText: 'Casais de Alta Performance' });
  await expect(card).toBeVisible();
  await expect(card).toContainText('Formulário próprio');
  await card.click();
  await expect(page.locator('.step-lbl')).toHaveText(['Produto', 'Contratante', 'Casal', 'Pagamento', 'Resumo']);
  await page.screenshot({ path: PRINTS + '12-casais-produtos.png' });
  await continuar(page);
  await etapa(page, 'Dados do contratante');

  // 2. O CNPJ completo: a tela pergunta ao backend, que pergunta ao Conecta.
  await contratantePJ(page, ATIVO);
  await expect(page.locator('[data-conferencia="ativo"]')).toContainText('Cliente ativo do Elite Empresarial');
  await expect.poll(async () => (await perguntasAoConecta(request)).length).toBe(1);
  const [pergunta] = await perguntasAoConecta(request);
  expect(pergunta).toMatchObject({ caminho: '/api/v1/clientes/ativo', chave_do_lobby: true, corpo: { cnpj: '22122190000152' } });
  expect(pergunta.user_agent).not.toMatch(/python-urllib/i);

  // 4. Sem e-mail nem telefone: não avança e diz o que falta.
  await continuar(page);
  await expect(faltas(page)).toHaveText(['o e-mail', 'o telefone']);
  await etapa(page, 'Dados do contratante');
  await page.screenshot({ path: PRINTS + '13-casais-contratante-faltando.png', fullPage: true });
  await contato(page);
  await expect(page.locator('[data-faltas]')).toHaveCount(0);
  await continuar(page);

  // 4. Um participante só, e depois o mesmo CPF nos dois.
  await etapa(page, 'Casal');
  await page.locator('#cas-p1-nome').fill(CASAL[0].nome);
  await page.locator('#cas-p1-cpf').fill(CASAL[0].cpf);
  await continuar(page);
  await expect(faltas(page)).toHaveText(['o nome do participante 2', 'o CPF do participante 2']);
  await page.locator('#cas-p2-nome').fill(CASAL[1].nome);
  await page.locator('#cas-p2-cpf').fill(CASAL[0].cpf);
  await continuar(page);
  await expect(faltas(page)).toHaveText(['um CPF diferente para cada participante (os dois estão com o mesmo)']);
  await etapa(page, 'Casal');
  await page.screenshot({ path: PRINTS + '14-casais-mesmo-cpf.png', fullPage: true });
  await page.locator('#cas-p2-cpf').fill(CASAL[1].cpf);
  await continuar(page);

  // 2. O valor do Elite, sem controle nenhum para trocar.
  await etapa(page, 'Pagamento');
  await expect(page.locator('[data-valor-casais]')).toHaveText('R$ 35.000,00');
  await expect(page.locator('#cas-elite')).toHaveCount(0);
  await expect(page.locator('[data-conferencia="ativo"]')).toBeVisible();
  // Sem a data: não avança.
  await continuar(page);
  await expect(faltas(page)).toHaveText(['a data de vencimento']);
  await pagamento(page);
  await page.screenshot({ path: PRINTS + '15-casais-pagamento-pj-elite.png', fullPage: true });
  await continuar(page);

  // Resumo: o valor e o número do contrato.
  await etapa(page, 'Confirme o pedido');
  await expect(page.locator('[data-valor-resumo]')).toHaveText('R$ 35.000,00');
  const numero = page.locator('[data-numero-contrato]');
  await expect(numero).toHaveText(/^CAP-\d{6}1\d{5}$/);
  const contrato = await numero.textContent();
  await page.screenshot({ path: PRINTS + '16-casais-resumo-pj-elite.png', fullPage: true });

  // 5. O envio: o POST ao webhook, e o número na tela de sucesso.
  await page.getByRole('button', { name: /Finalizar cadastro/ }).click();
  await expect(page.locator('[data-protocolo]')).toHaveText(contrato);
  await expect(page.getByText('Número do contrato')).toBeVisible();
  await page.screenshot({ path: PRINTS + '17-casais-sucesso.png', fullPage: true });
  expect(n8n.vendas).toHaveLength(1);
  expect(n8n.outros).toEqual([]);
  const [venda] = n8n.vendas;
  expect(venda).toMatchObject({
    id_venda: contrato,
    numero_contrato: contrato,
    produto: 'CASAIS_DE_ALTA_PERFORMANCE',
    contratante: {
      tipo_pessoa: 'PJ',
      nome: EMPRESA.razao_social,
      documento: ATIVO,
      email: 'financeiro@quintalazul.com.br',
      telefone: '(48) 99876-1234',
      endereco: {
        logradouro: 'Rua das Acácias', numero: '210', complemento: 'Sala 3',
        bairro: 'Centro', cidade: 'Florianópolis', uf: 'SC', cep: '88010-400',
      },
    },
    participantes: CASAL,
    valor: 35000,
    tabela: 'elite',
    elite_conferido_no_conecta: true,
    pagamento: { forma: 'cartao', parcelas: 12, data: VENCIMENTO },
    comprovante_erro: null,
    enviado_por: { email: EMAIL },
  });
  expect(venda.comprovante).toMatch(/^[0-9a-f-]{36}\.[0-9a-f]+$/);
  expect(venda.comprovante_valores).toEqual({
    fluxo: 'casais', categoria_id: 'produtos', produto_id: 'casais-alta-performance', negociado: false,
    valor_total: 35000, valor_mensal: null, entrada: 35000, cronograma: [], protocolo: contrato,
    casais: { tipo_pessoa: 'PJ', documento: ATIVO, tabela: 'elite', elite_conferido_no_conecta: true },
  });
  // O comprovante reusou a resposta guardada: o Conecta foi perguntado uma vez.
  expect(await perguntasAoConecta(request)).toHaveLength(1);

  // 6. O n8n confere: um valor fora dos dois é recusado; a venda de verdade passa.
  const fora = await validar(request, venda, { ...venda.comprovante_valores, valor_total: 30000, entrada: 30000 });
  expect(fora).toMatchObject({ valido: false, motivo: 'valores_invalidos' });
  expect(await validar(request, venda)).toMatchObject({ valido: true, fluxo: 'casais', protocolo: contrato });
});

test('PJ não ativa: R$ 39.997,00, e o comprovante dela não vira os R$ 35.000,00', async ({ page, request }) => {
  const n8n = await n8nFalso(page);
  await entrar(page);
  await abrirCasais(page);
  await contratantePJ(page, INATIVO);
  await expect(page.locator('[data-conferencia="inativo"]')).toContainText('não tem este CNPJ como cliente ativo');
  await contato(page);
  await continuar(page);
  await casal(page);
  await continuar(page);
  await etapa(page, 'Pagamento');
  await expect(page.locator('[data-valor-casais]')).toHaveText('R$ 39.997,00');
  await expect(page.locator('#cas-elite')).toHaveCount(0);
  await pagamento(page, { forma: 'Boleto' });
  await continuar(page);
  await expect(page.locator('[data-valor-resumo]')).toHaveText('R$ 39.997,00');
  await expect(page.locator('[data-numero-contrato]')).toHaveText(/^CAP-\d{6}3\d{5}$/);
  await page.getByRole('button', { name: /Finalizar cadastro/ }).click();
  await expect(page.locator('[data-protocolo]')).toBeVisible();

  const [venda] = n8n.vendas;
  expect(venda).toMatchObject({
    valor: 39997, tabela: 'cheio', elite_conferido_no_conecta: true,
    pagamento: { forma: 'boleto', parcelas: 1, data: VENCIMENTO },
  });
  // 6. Os R$ 35.000,00 numa PJ que o Conecta diz não ser ativa: recusado.
  const elite = {
    ...venda.comprovante_valores, valor_total: 35000, entrada: 35000,
    casais: { ...venda.comprovante_valores.casais, tabela: 'elite' },
  };
  expect(await validar(request, venda, elite)).toMatchObject({ valido: false, motivo: 'valores_adulterados' });
  expect(await validar(request, venda)).toMatchObject({ valido: true });
});

test('PF: marcar e desmarcar "paga o valor do Elite Empresarial" alterna o valor', async ({ page, request }) => {
  const n8n = await n8nFalso(page);
  await entrar(page);
  await abrirCasais(page);
  await page.getByRole('button', { name: /Pessoa física/ }).click();
  await page.locator('#cas-nome').fill(PF.nome);
  await page.locator('#cas-cpf').fill(PF.cpf);
  await contato(page);
  await page.locator('#cas-cep').fill('88010400');
  await page.getByRole('button', { name: 'Buscar endereço pelo CEP' }).click();
  await expect(page.locator('#cas-cidade')).toHaveValue('Florianópolis');
  await continuar(page);
  // O número ficou faltando: o CEP não traz.
  await expect(faltas(page)).toHaveText(['o número do endereço']);
  await page.locator('#cas-numero').fill('88');
  await continuar(page);
  await casal(page);
  await continuar(page);

  await etapa(page, 'Pagamento');
  const valor = page.locator('[data-valor-casais]');
  const elite = page.locator('#cas-elite');
  await expect(valor).toHaveText('R$ 39.997,00');
  await elite.check();
  await expect(valor).toHaveText('R$ 35.000,00');
  await page.screenshot({ path: PRINTS + '18-casais-pf-elite-marcado.png', fullPage: true });
  await elite.uncheck();
  await expect(valor).toHaveText('R$ 39.997,00');
  await elite.check();
  await expect(valor).toHaveText('R$ 35.000,00');
  await pagamento(page, { forma: 'Pix' });
  await continuar(page);
  await expect(page.locator('[data-valor-resumo]')).toHaveText('R$ 35.000,00');
  await expect(page.locator('[data-numero-contrato]')).toHaveText(/^CAP-\d{6}0\d{5}$/);
  await page.getByRole('button', { name: /Finalizar cadastro/ }).click();
  await expect(page.locator('[data-protocolo]')).toBeVisible();

  const [venda] = n8n.vendas;
  expect(venda).toMatchObject({
    contratante: { tipo_pessoa: 'PF', nome: PF.nome, documento: PF.cpf, endereco: { numero: '88' } },
    valor: 35000, tabela: 'elite', elite_conferido_no_conecta: false,
    pagamento: { forma: 'pix', parcelas: 1, data: VENCIMENTO },
  });
  expect(await validar(request, venda)).toMatchObject({ valido: true });
  // Na PF o Conecta não é perguntado.
  expect(await perguntasAoConecta(request)).toEqual([]);
});

// Por último: a falha deixa o backend 60 s sem perguntar ao Conecta (a pausa é
// de todos), e o teste seguinte receberia "sem resposta".
test('Conecta fora: fica no preço cheio, com o aviso, e o Elite não sai', async ({ page }) => {
  const n8n = await n8nFalso(page);
  await entrar(page);
  await abrirCasais(page);
  await contratantePJ(page, FORA_DO_AR);
  const aviso = page.locator('[data-conferencia="sem_resposta"]');
  await expect(aviso).toContainText('Não foi possível conferir o CNPJ no Conecta agora');
  await expect(aviso).toContainText('preço cheio, R$ 39.997,00');
  await expect(aviso.getByRole('button', { name: 'Conferir de novo' })).toBeVisible();
  await contato(page);
  await continuar(page);
  await casal(page);
  await continuar(page);
  await etapa(page, 'Pagamento');
  await expect(page.locator('[data-valor-casais]')).toHaveText('R$ 39.997,00');
  await page.screenshot({ path: PRINTS + '19-casais-conecta-fora.png', fullPage: true });
  await pagamento(page, { forma: 'Pix' });
  await continuar(page);
  await expect(page.getByText('Preço cheio (sem conferência do Conecta)')).toBeVisible();
  await page.getByRole('button', { name: /Finalizar cadastro/ }).click();
  await expect(page.locator('[data-protocolo]')).toBeVisible();
  const [venda] = n8n.vendas;
  expect(venda).toMatchObject({ valor: 39997, tabela: 'cheio', elite_conferido_no_conecta: false });
  expect(venda.comprovante).toBeTruthy();
});
