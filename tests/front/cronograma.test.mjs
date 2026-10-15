// As datas do cronograma do `index.html` (TSK-1077), sem navegador.
//
//     node --test tests/front/cronograma.test.mjs
//
// As datas vão com a venda para o n8n e o Omie, e o servidor do Lobby confere
// só os valores — um erro aqui não para em lugar nenhum antes de virar cobrança.
// O teste roda as funções DO PRÓPRIO index.html (o trecho de JS puro entre
// `const round2` e o componente "Logo", onde estão a regra, as datas e o
// `initPayment`), com o relógio trocado pela data da venda, e compara:
//
// - dia fixo LIGADO com a fórmula de antes da TSK-1077, copiada abaixo: com ele
//   ligado nada pode mudar, venda a venda, de 2026 a 2028;
// - dia fixo DESLIGADO com uma conta independente (tabela de dias do mês, sem
//   os atalhos do Date): o dia da venda, a partir do mês seguinte, e o último
//   dia do mês que não o tem.
//
// O CI roda com TZ=America/Sao_Paulo e com TZ=UTC.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8');
const inicio = html.indexOf('const round2');
const fim = html.indexOf('/* ===== Logo');
assert.ok(inicio > 0 && fim > inicio, 'o trecho das datas sumiu do index.html — ajuste os marcadores');

const DataDeVerdade = Date;
let hoje = null;
class DataDaVenda extends DataDeVerdade {
  constructor(...args) { if (args.length === 0) super(hoje); else super(...args); }
  static now() { return new DataDeVerdade(hoje).getTime(); }
}
globalThis.Date = DataDaVenda;
const lobby = new Function(
  html.slice(inicio, fim).replace(/^let COBRANCA/m, 'var COBRANCA').replace(/^let CATS/m, 'var CATS')
  + '\nreturn { parcelaVencimento, inicioDasParcelas, diaDasParcelas, dataDaEntrada, normalizaCobranca, initPayment };',
)();

const PADRAO = { dia_vencimento: 15, primeiro_vencimento: 'mes_seguinte', entrada_prazo_dias: 0, dia_fixo: true };

// Monta o cronograma como o `initPayment`: a regra normalizada, a entrada pelo
// prazo, as parcelas a partir de `inicioDasParcelas`.
function cronograma(venda, regra, n = 11) {
  hoje = `${venda}T12:00:00`;
  const cob = lobby.normalizaCobranca(regra, PADRAO);
  const entrada = lobby.dataDaEntrada(cob);
  const base = lobby.inicioDasParcelas(entrada, cob, venda);
  return { entrada, parcelas: Array.from({ length: n }, (_, i) => lobby.parcelaVencimento(base, i, cob)) };
}

// ----- referências -----------------------------------------------------------

const pad = (n) => String(n).padStart(2, '0');
const bissexto = (a) => (a % 4 === 0 && a % 100 !== 0) || a % 400 === 0;
const diasNoMes = (a, m) => [31, bissexto(a) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];

function desligadoDeReferencia(venda, n) {
  const [a, m, d] = venda.split('-').map(Number);
  return Array.from({ length: n }, (_, i) => {
    const total = (m - 1) + 1 + i;
    const ano = a + Math.floor(total / 12);
    const mes = (total % 12) + 1;
    return `${ano}-${pad(mes)}-${pad(Math.min(d, diasNoMes(ano, mes)))}`;
  });
}

// A conta do index.html antes da TSK-1077 (main ca5be14), sem mudar uma vírgula.
function ligadoDeAntes(venda, regra, n) {
  const [va, vm, vd] = venda.split('-').map(Number);
  let base = venda;
  if (regra.entrada_prazo_dias) {
    const e = new DataDeVerdade(va, vm - 1, vd + regra.entrada_prazo_dias);
    base = `${e.getFullYear()}-${pad(e.getMonth() + 1)}-${pad(e.getDate())}`;
  }
  const [ano, mes, dia] = base.split('-').map(Number);
  return Array.from({ length: n }, (_, idx) => {
    const antecipa = regra.primeiro_vencimento === 'proximo' && dia < regra.dia_vencimento ? 1 : 0;
    const dt = new DataDeVerdade(ano, mes - antecipa + idx, regra.dia_vencimento);
    return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(regra.dia_vencimento)}`;
  });
}

function* dias(de, ate) {
  for (let t = new DataDeVerdade(de, 0, 1, 12); t.getFullYear() <= ate; t = new DataDeVerdade(t.getFullYear(), t.getMonth(), t.getDate() + 1, 12)) {
    yield `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
  }
}

const DESLIGADO = { ...PADRAO, dia_fixo: false };

// ----- os casos do contrato --------------------------------------------------

test('venda em 14/10: parcelas 14/11, 14/12, 14/01…; entrada no dia', () => {
  const c = cronograma('2026-10-14', DESLIGADO, 4);
  assert.equal(c.entrada, '2026-10-14');
  assert.deepEqual(c.parcelas, ['2026-11-14', '2026-12-14', '2027-01-14', '2027-02-14']);
});

test('venda em 31/01: último dia de fevereiro, e o dia 31 volta depois', () => {
  assert.deepEqual(cronograma('2027-01-31', DESLIGADO, 5).parcelas, ['2027-02-28', '2027-03-31', '2027-04-30', '2027-05-31', '2027-06-30']);
  assert.deepEqual(cronograma('2028-01-31', DESLIGADO, 2).parcelas, ['2028-02-29', '2028-03-31']);
});

test('29 e 30 em fevereiro, com e sem bissexto; 31/03 → 30/04', () => {
  assert.deepEqual(cronograma('2027-01-29', DESLIGADO, 2).parcelas, ['2027-02-28', '2027-03-29']);
  assert.deepEqual(cronograma('2028-01-29', DESLIGADO, 2).parcelas, ['2028-02-29', '2028-03-29']);
  assert.deepEqual(cronograma('2027-01-30', DESLIGADO, 2).parcelas, ['2027-02-28', '2027-03-30']);
  assert.deepEqual(cronograma('2027-03-31', DESLIGADO, 2).parcelas, ['2027-04-30', '2027-05-31']);
});

test('virada de ano', () => {
  assert.deepEqual(cronograma('2026-12-31', DESLIGADO, 3).parcelas, ['2027-01-31', '2027-02-28', '2027-03-31']);
  assert.deepEqual(cronograma('2026-12-15', DESLIGADO, 2).parcelas, ['2027-01-15', '2027-02-15']);
});

test('entrada no prazo de hoje, mesmo caindo depois da 1ª parcela', () => {
  const c = cronograma('2026-10-14', { ...DESLIGADO, entrada_prazo_dias: 45 }, 2);
  assert.equal(c.entrada, '2026-11-28');
  assert.deepEqual(c.parcelas, ['2026-11-14', '2026-12-14']);
});

test('desligado ignora o dia do vencimento e a primeira parcela guardados', () => {
  const regra = { dia_vencimento: 5, primeiro_vencimento: 'proximo', entrada_prazo_dias: 0, dia_fixo: false };
  assert.deepEqual(cronograma('2026-10-03', regra, 2).parcelas, ['2026-11-03', '2026-12-03']);
});

test('desligado parte da data da venda guardada, não do relógio nem da entrada', () => {
  hoje = '2026-11-01T00:01:00';
  const cob = lobby.normalizaCobranca(DESLIGADO, PADRAO);
  // Cronograma montado em 31/10, ação depois da meia-noite, entrada mudada à mão.
  assert.equal(lobby.parcelaVencimento(lobby.inicioDasParcelas('2026-11-20', cob, '2026-10-31'), 0, cob), '2026-11-30');
  assert.equal(lobby.diaDasParcelas(cob, '2026-10-31'), 31);
  assert.equal(lobby.diaDasParcelas(lobby.normalizaCobranca(PADRAO, PADRAO), '2026-10-31'), 15);
});

test('initPayment: refazer o cronograma depois da meia-noite mantém o dia da venda', () => {
  const produto = { id: 'pro', price: 155964, monthly: 12997, recurring: true, vigencia: 12 };
  const desligada = { cobranca: DESLIGADO };
  hoje = '2026-10-31T23:50:00';
  const aberta = lobby.initPayment(produto, desligada);
  assert.equal(aberta.vendaData, '2026-10-31');
  assert.deepEqual(aberta.parcelas.slice(0, 2).map((p) => p.data), ['2026-11-30', '2026-12-31']);
  // Sair da edição / encerrar a autorização já em 01/11: a venda segue a de 31/10.
  hoje = '2026-11-01T00:05:00';
  const refeita = lobby.initPayment(produto, desligada, aberta.vendaData);
  assert.equal(refeita.vendaData, '2026-10-31');
  assert.deepEqual(refeita.parcelas.map((p) => p.data), aberta.parcelas.map((p) => p.data));
  assert.equal(refeita.parcelas.length, 11);
  // Com prazo, a entrada também parte da venda de 31/10, e não do relógio.
  const comPrazo = { cobranca: { ...DESLIGADO, entrada_prazo_dias: 10 } };
  hoje = '2026-10-31T23:50:00';
  const abertaComPrazo = lobby.initPayment(produto, comPrazo);
  assert.equal(abertaComPrazo.entradaData, '2026-11-10');
  hoje = '2026-11-01T00:05:00';
  const refeitaComPrazo = lobby.initPayment(produto, comPrazo, abertaComPrazo.vendaData);
  assert.equal(refeitaComPrazo.entradaData, '2026-11-10');
  assert.deepEqual(refeitaComPrazo.parcelas.map((p) => p.data), abertaComPrazo.parcelas.map((p) => p.data));
  // Venda nova (sem data anterior) começa no dia de hoje.
  assert.deepEqual(lobby.initPayment(produto, desligada).parcelas[0].data, '2026-12-01');
  // Ligado, a data da venda não entra: igual a antes, pela entrada de hoje.
  const ligada = { cobranca: { ...PADRAO, entrada_prazo_dias: 10 } };
  assert.equal(lobby.initPayment(produto, ligada, '2026-10-31').entradaData, '2026-11-11');
  assert.deepEqual(
    lobby.initPayment(produto, ligada, '2026-10-31').parcelas.map((p) => p.data),
    lobby.initPayment(produto, ligada).parcelas.map((p) => p.data),
  );
  assert.equal(lobby.initPayment(produto, ligada).parcelas[0].data, '2026-12-15');
  assert.equal(lobby.initPayment(produto, { cobranca: PADRAO }, '2026-10-31').entradaData, '2026-11-01');
});

test('sem dia_fixo no JSON (backend de antes) ou com texto, fica ligado', () => {
  const semCampo = { dia_vencimento: 15, primeiro_vencimento: 'mes_seguinte', entrada_prazo_dias: 0 };
  assert.deepEqual(cronograma('2026-10-14', semCampo, 1).parcelas, ['2026-11-15']);
  assert.deepEqual(cronograma('2026-10-14', { ...semCampo, dia_fixo: 'false' }, 1).parcelas, ['2026-11-15']);
  assert.equal(lobby.normalizaCobranca({}, DESLIGADO).dia_fixo, false, 'exceção sem o campo herda o da categoria');
});

// ----- as varreduras ---------------------------------------------------------

test('desligado: toda venda de 2026 a 2029 bate com a conta de referência', () => {
  for (const venda of dias(2026, 2029)) {
    const c = cronograma(venda, DESLIGADO, 24);
    assert.deepEqual(c.parcelas, desligadoDeReferencia(venda, 24), `venda ${venda}`);
    assert.ok(c.parcelas.every((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)), 'formato do payload: AAAA-MM-DD');
  }
});

test('ligado: toda venda de 2026 a 2028 sai igual à de antes da TSK-1077', () => {
  const regras = [];
  for (const dia of [1, 10, 15, 28]) for (const pv of ['mes_seguinte', 'proximo']) for (const prazo of [0, 10, 45]) {
    regras.push({ dia_vencimento: dia, primeiro_vencimento: pv, entrada_prazo_dias: prazo });
  }
  for (const venda of dias(2026, 2028)) {
    for (const regra of regras) {
      const c = cronograma(venda, { ...regra, dia_fixo: true }, 11);
      assert.deepEqual(c.parcelas, ligadoDeAntes(venda, regra, 11), `venda ${venda} ${JSON.stringify(regra)}`);
    }
  }
});
