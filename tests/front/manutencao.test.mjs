// O login central em manutenção, nas três páginas que chamam o Conecta
// (TSK-1238), sem navegador.
//
//     node --test tests/front/manutencao.test.mjs
//
// O backend responde 503 `{erro: "manutencao", mensagem, previsao}` quando o
// Conecta está em manutenção (login, esqueci e definir senha). A tela troca o
// erro pelo aviso "Temporariamente indisponível / Em manutenção", com o texto
// do aviso e a previsão de volta no horário de Brasília. O teste roda o trecho
// de JS puro de cada página (entre os marcadores abaixo) e confere:
//
// - que o trecho é o MESMO nas três (são arquivos sem build, copiados à mão);
// - o texto da previsão, em qualquer fuso da máquina;
// - que só o 503 com `erro: "manutencao"` vira manutenção: o 503 de sempre
//   continua sendo o erro "indisponível".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const INICIO = '/* ===== Login central em manutenção (TSK-1238) ===== */';
const FIM = '/* ===== fim do login central em manutenção ===== */';
const PAGINAS = ['index.html', 'esqueci-senha.html', 'definir-senha.html'];

const ler = (pagina) => readFileSync(new URL(`../../${pagina}`, import.meta.url), 'utf8');

function trecho(pagina) {
  const html = ler(pagina);
  const inicio = html.indexOf(INICIO);
  const fim = html.indexOf(FIM);
  assert.ok(inicio > 0 && fim > inicio, `o trecho da manutenção sumiu do ${pagina}`);
  return html.slice(inicio, fim + FIM.length);
}

const { textoDaPrevisao, erroDeManutencao } = new Function(
  trecho('index.html') + '\nreturn { textoDaPrevisao, erroDeManutencao };',
)();

test('o trecho é o mesmo nas três páginas', () => {
  const doIndex = trecho('index.html');
  for (const pagina of PAGINAS.slice(1)) assert.equal(trecho(pagina), doIndex, pagina);
});

test('previsão no mesmo dia de Brasília: "hoje às HH:MM"', () => {
  const agora = new Date('2026-10-26T13:00:00-03:00');
  assert.equal(textoDaPrevisao('2026-10-26T15:00:00-03:00', agora), 'hoje às 15:00');
  // Mesmo instante escrito em UTC: a tela mostra a hora de Brasília.
  assert.equal(textoDaPrevisao('2026-10-26T18:30:00+00:00', agora), 'hoje às 15:30');
});

test('previsão em outro dia: "DD/MM às HH:MM", pelo dia de Brasília', () => {
  const agora = new Date('2026-10-26T13:00:00-03:00');
  assert.equal(textoDaPrevisao('2026-10-27T09:05:00-03:00', agora), '27/10 às 09:05');
  // 23h30 de Brasília ainda é hoje, embora já seja amanhã em UTC.
  assert.equal(textoDaPrevisao('2026-10-27T02:30:00Z', agora), 'hoje às 23:30');
  // Meia-noite de Brasília já é amanhã, e a hora sai 00, não 24.
  assert.equal(textoDaPrevisao('2026-10-27T00:00:00-03:00', agora), '27/10 às 00:00');
});

test('sem previsão, ou ilegível, a tela não fala de hora', () => {
  for (const valor of [null, undefined, '', 'amanhã cedo', 'não é data']) {
    assert.equal(textoDaPrevisao(valor), null, String(valor));
  }
});

test('o 503 de manutenção vira o aviso, com a mensagem e a previsão', () => {
  const erro = erroDeManutencao(
    { status: 503 },
    { erro: 'manutencao', mensagem: 'Atualização do sistema.', previsao: '2026-10-26T15:00:00-03:00' },
  );
  assert.ok(erro instanceof Error);
  assert.equal(erro.manutencao.mensagem, 'Atualização do sistema.');
  assert.match(erro.manutencao.previsao, /15:00$/);
});

test('manutenção sem texto ainda tem frase', () => {
  const erro = erroDeManutencao({ status: 503 }, { erro: 'manutencao', mensagem: null, previsao: null });
  assert.ok(erro.manutencao.mensagem);
  assert.equal(erro.manutencao.previsao, null);
});

test('o 503 de sempre (Conecta fora do ar) não é manutenção', () => {
  assert.equal(erroDeManutencao({ status: 503 }, { erro: 'O sistema de login está indisponível.' }), null);
  assert.equal(erroDeManutencao({ status: 503 }, {}), null);
  assert.equal(erroDeManutencao({ status: 401 }, { erro: 'manutencao' }), null);
});

test('as três telas mostram "Temporariamente indisponível / Em manutenção"', () => {
  for (const pagina of PAGINAS) {
    const html = ler(pagina);
    assert.ok(html.includes('Temporariamente indisponível'), pagina);
    assert.ok(html.includes('Em manutenção'), pagina);
    assert.ok(html.includes('erroDeManutencao(r, '), `${pagina} não usa o erroDeManutencao no fetch`);
  }
});
