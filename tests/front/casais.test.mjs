// O Casais de Alta Performance no `index.html` (TSK-1622), sem navegador.
//
//     node --test tests/front/casais.test.mjs
//
// Roda as funções DO PRÓPRIO index.html (o trecho de JS puro entre as máscaras e
// o componente "Logo": validadores, `buildProtocol` e o bloco do Casais) e
// confere o que a tela decide sozinha antes de o servidor conferir:
//
// - o valor: na PF, o "paga o valor do Elite Empresarial"; na PJ, só o Conecta
//   dizendo "ativo" para ESTE CNPJ dá o Elite — o resto é o cheio;
// - o que falta em cada etapa (e-mail, telefone, os dois participantes, o mesmo
//   CPF nos dois);
// - o payload do n8n, chave a chave (é o contrato), e o que o servidor assina;
// - o número do contrato (o protocolo CAP-AAMMDDPRRRRR);
// - a pergunta ao backend: só o fim da sessão lança, o resto é "sem resposta".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8');
const inicio = html.indexOf('// ===== Máscaras =====');
const fim = html.indexOf('/* ===== Logo');
assert.ok(inicio > 0 && fim > inicio, 'o trecho do Casais sumiu do index.html — ajuste os marcadores');

// `apiFetch` e `fimDeSessao` moram fora do trecho (na sessão do Lobby): o teste
// passa os dele. O teto de parcelas mora no topo, com o webhook: vem de lá.
const resposta = { atual: null };
const parcelasMax = Number((html.match(/^const CASAIS_MAX_PARCELAS_CARTAO = (\d+);/m) || [])[1]);
assert.equal(parcelasMax, 12, 'CASAIS_MAX_PARCELAS_CARTAO sumiu do topo do index.html');
const lobby = new Function(
  'apiFetch', 'fimDeSessao', 'CASAIS_MAX_PARCELAS_CARTAO',
  html.slice(inicio, fim).replace(/^let COBRANCA/m, 'var COBRANCA').replace(/^let CATS/m, 'var CATS')
  + '\nreturn { tabelaDoCasais, valorDoCasais, conferidoNoConecta, parcelasDoCasais, faltasDoContratanteCasais,'
  + ' faltasDoCasal, faltasDoPagamentoCasais, payloadDoCasais, valoresDoCasais, numeroDoContratoCasais,'
  + ' perguntaEliteCasais, conferenciaVaziaCasais, CASAIS_VALOR_CHEIO, CASAIS_VALOR_ELITE };',
)(
  async (...args) => resposta.atual(...args),
  (e) => !!(e && (e.sessaoDerrubada || e.sessaoTrocada)),
  parcelasMax,
);

const CNPJ = '22.122.190/0001-52';
const OUTRO_CNPJ = '37.598.418/0001-52';
const conferencia = (estado, cnpj = CNPJ) => ({ cnpj: cnpj.replace(/\D/g, ''), estado, mensagem: '', tentarEm: null });

// Uma venda preenchida, PJ: como o estado `s` do App.
function venda(mudancas = {}) {
  return {
    casTipoPessoa: 'PJ', cnpj: CNPJ, razaoSocial: 'Quintal Azul Comércio de Plantas Ltda',
    casNome: '', casCpf: '', casEmail: 'financeiro@quintalazul.com.br', casTelefone: '(48) 99876-1234',
    cep: '88010-400', endereco: 'Rua das Acácias', numero: '210', complemento: 'Sala 3',
    bairro: 'Centro', cidade: 'Florianópolis', estado: 'SC',
    casParticipantes: [{ nome: 'Helena Duarte Vasconcelos', cpf: '294.199.881-04' }, { nome: 'Otávio Brandão Lacerda', cpf: '450.490.714-37' }],
    casEliteMarcado: false, casElite: conferencia('ativo'),
    casForma: 'cartao', casParcelas: 12, casData: '2026-12-10', casProtocolo: '',
    ...mudancas,
  };
}
const pf = (mudancas = {}) => venda({
  casTipoPessoa: 'PF', cnpj: '', razaoSocial: '', casNome: 'Marina Quintela Rocha', casCpf: '775.927.214-65',
  casElite: lobby.conferenciaVaziaCasais(), ...mudancas,
});

test('os dois valores são os do dono', () => {
  assert.equal(lobby.CASAIS_VALOR_CHEIO, 39997);
  assert.equal(lobby.CASAIS_VALOR_ELITE, 35000);
  assert.equal(lobby.valorDoCasais('elite'), 35000);
  assert.equal(lobby.valorDoCasais('cheio'), 39997);
});

test('PF: o consultor marca e desmarca o Elite, e o Conecta não conta', () => {
  assert.equal(lobby.tabelaDoCasais(pf({ casEliteMarcado: true })), 'elite');
  assert.equal(lobby.tabelaDoCasais(pf({ casEliteMarcado: false })), 'cheio');
  // Uma conferência que tenha sobrado de quando era PJ não vale na PF.
  assert.equal(lobby.tabelaDoCasais(pf({ casEliteMarcado: false, casElite: conferencia('ativo') })), 'cheio');
  assert.equal(lobby.conferidoNoConecta(pf({ casEliteMarcado: true })), false);
});

test('PJ: só o Conecta dizendo "ativo" para este CNPJ dá o Elite', () => {
  assert.equal(lobby.tabelaDoCasais(venda()), 'elite');
  for (const estado of ['inativo', 'sem_resposta', 'conferindo', 'idle']) {
    assert.equal(lobby.tabelaDoCasais(venda({ casElite: conferencia(estado) })), 'cheio', estado);
  }
  // A resposta de outro CNPJ não vale (o consultor trocou o CNPJ).
  assert.equal(lobby.tabelaDoCasais(venda({ casElite: conferencia('ativo', OUTRO_CNPJ) })), 'cheio');
  // Nem marcar a caixa da PF muda a PJ.
  assert.equal(lobby.tabelaDoCasais(venda({ casElite: conferencia('inativo'), casEliteMarcado: true })), 'cheio');
});

test('PJ: conferido no Conecta é ele ter respondido, sim ou não', () => {
  assert.equal(lobby.conferidoNoConecta(venda({ casElite: conferencia('ativo') })), true);
  assert.equal(lobby.conferidoNoConecta(venda({ casElite: conferencia('inativo') })), true);
  assert.equal(lobby.conferidoNoConecta(venda({ casElite: conferencia('sem_resposta') })), false);
  assert.equal(lobby.conferidoNoConecta(venda({ casElite: conferencia('ativo', OUTRO_CNPJ) })), false);
});

test('contratante: diz o que falta, e nada quando está completo', () => {
  assert.deepEqual(lobby.faltasDoContratanteCasais(venda()), []);
  assert.deepEqual(lobby.faltasDoContratanteCasais(pf()), []);
  const faltas = lobby.faltasDoContratanteCasais(venda({ casEmail: '', casTelefone: '' }));
  assert.deepEqual(faltas, ['o e-mail', 'o telefone']);
  assert.deepEqual(lobby.faltasDoContratanteCasais(venda({ casEmail: 'financeiro@', casTelefone: '(48) 9987' })),
    ['um e-mail válido', 'um telefone válido, com DDD']);
  assert.deepEqual(lobby.faltasDoContratanteCasais(pf({ casNome: '', casCpf: '775.927.214-66' })),
    ['o nome completo do contratante', 'um CPF válido do contratante']);
  assert.ok(lobby.faltasDoContratanteCasais(venda({ cnpj: '22.122.190/0001-53' })).includes('um CNPJ válido'));
  assert.ok(lobby.faltasDoContratanteCasais(venda({ cep: '', numero: '' })).includes('o número do endereço'));
});

test('casal: os dois participantes, com CPF válido e diferente', () => {
  assert.deepEqual(lobby.faltasDoCasal(venda().casParticipantes), []);
  assert.deepEqual(
    lobby.faltasDoCasal([{ nome: 'Helena Duarte Vasconcelos', cpf: '294.199.881-04' }, { nome: '', cpf: '' }]),
    ['o nome do participante 2', 'o CPF do participante 2'],
  );
  assert.deepEqual(lobby.faltasDoCasal([]), [
    'o nome do participante 1', 'o CPF do participante 1', 'o nome do participante 2', 'o CPF do participante 2',
  ]);
  assert.deepEqual(
    lobby.faltasDoCasal([{ nome: 'Helena Duarte Vasconcelos', cpf: '294.199.881-04' }, { nome: 'Otávio Brandão Lacerda', cpf: '29419988104' }]),
    ['um CPF diferente para cada participante (os dois estão com o mesmo)'],
  );
  assert.deepEqual(
    lobby.faltasDoCasal([{ nome: 'Helena Duarte Vasconcelos', cpf: '294.199.881-05' }, { nome: 'Otávio Brandão Lacerda', cpf: '450.490.714-37' }]),
    ['um CPF válido do participante 1'],
  );
});

test('pagamento: a data, e esperar a conferência do Conecta na PJ', () => {
  assert.deepEqual(lobby.faltasDoPagamentoCasais(venda()), []);
  assert.deepEqual(lobby.faltasDoPagamentoCasais(venda({ casData: '' })), ['a data de vencimento']);
  assert.deepEqual(lobby.faltasDoPagamentoCasais(venda({ casElite: conferencia('conferindo') })),
    ['a conferência do CNPJ no Conecta terminar']);
  assert.deepEqual(lobby.faltasDoPagamentoCasais(pf({ casForma: 'cheque' })), ['a forma de pagamento']);
});

test('parcelas: só no cartão, até 12', () => {
  assert.equal(lobby.parcelasDoCasais({ casForma: 'cartao', casParcelas: 12 }), 12);
  assert.equal(lobby.parcelasDoCasais({ casForma: 'cartao', casParcelas: 30 }), 12);
  assert.equal(lobby.parcelasDoCasais({ casForma: 'cartao', casParcelas: 0 }), 1);
  assert.equal(lobby.parcelasDoCasais({ casForma: 'boleto', casParcelas: 6 }), 1);
  assert.equal(lobby.parcelasDoCasais({ casForma: 'pix', casParcelas: 6 }), 1);
});

test('o payload do n8n é o JSON combinado, chave a chave', () => {
  const payload = lobby.payloadDoCasais(venda(), 'CAP-261125112345');
  assert.deepEqual(payload, {
    id_venda: 'CAP-261125112345',
    numero_contrato: 'CAP-261125112345',
    produto: 'CASAIS_DE_ALTA_PERFORMANCE',
    contratante: {
      tipo_pessoa: 'PJ',
      nome: 'Quintal Azul Comércio de Plantas Ltda',
      documento: '22.122.190/0001-52',
      email: 'financeiro@quintalazul.com.br',
      telefone: '(48) 99876-1234',
      endereco: {
        logradouro: 'Rua das Acácias', numero: '210', complemento: 'Sala 3',
        bairro: 'Centro', cidade: 'Florianópolis', uf: 'SC', cep: '88010-400',
      },
    },
    participantes: [
      { nome: 'Helena Duarte Vasconcelos', cpf: '294.199.881-04' },
      { nome: 'Otávio Brandão Lacerda', cpf: '450.490.714-37' },
    ],
    valor: 35000,
    tabela: 'elite',
    elite_conferido_no_conecta: true,
    pagamento: { forma: 'cartao', parcelas: 12, data: '2026-12-10' },
  });
});

test('o payload da PF', () => {
  const payload = lobby.payloadDoCasais(pf({ casForma: 'boleto', casParcelas: 4, casEliteMarcado: false }), 'CAP-261125312345');
  assert.deepEqual(payload.contratante.tipo_pessoa, 'PF');
  assert.deepEqual(payload.contratante.nome, 'Marina Quintela Rocha');
  assert.deepEqual(payload.contratante.documento, '775.927.214-65');
  assert.deepEqual([payload.valor, payload.tabela, payload.elite_conferido_no_conecta], [39997, 'cheio', false]);
  assert.deepEqual(payload.pagamento, { forma: 'boleto', parcelas: 1, data: '2026-12-10' });
});

test('o que o servidor assina sai do payload', () => {
  const payload = lobby.payloadDoCasais(venda({ casElite: conferencia('inativo') }), 'CAP-261125112345');
  assert.deepEqual(lobby.valoresDoCasais(payload, 'produtos', 'casais-alta-performance'), {
    fluxo: 'casais',
    categoria_id: 'produtos',
    produto_id: 'casais-alta-performance',
    negociado: false,
    valor_total: 39997,
    valor_mensal: null,
    entrada: 39997,
    cronograma: [],
    protocolo: 'CAP-261125112345',
    casais: { tipo_pessoa: 'PJ', documento: '22.122.190/0001-52', tabela: 'cheio', elite_conferido_no_conecta: true },
  });
});

test('o número do contrato é o protocolo CAP-AAMMDDPRRRRR, e fica o mesmo até a forma mudar', () => {
  const produto = { sigla: 'CAP' };
  const dia = new Date(2026, 10, 25, 15, 0, 0);
  const pix = lobby.numeroDoContratoCasais('', produto, 'pix', dia);
  assert.match(pix, /^CAP-2611250\d{5}$/);
  assert.equal(lobby.numeroDoContratoCasais(pix, produto, 'pix', dia), pix);
  assert.match(lobby.numeroDoContratoCasais(pix, produto, 'cartao', dia), /^CAP-2611251\d{5}$/);
  assert.match(lobby.numeroDoContratoCasais(pix, produto, 'boleto', dia), /^CAP-2611253\d{5}$/);
  // De outro dia, nasce outro.
  assert.match(lobby.numeroDoContratoCasais(pix, produto, 'pix', new Date(2026, 10, 26, 9, 0, 0)), /^CAP-2611260\d{5}$/);
});

test('a pergunta ao backend: o 200 do contrato, e o resto é "sem resposta"', async () => {
  let pedido = null;
  const json = (status, corpo, cabecalhos = {}) => async (url, opcoes) => {
    pedido = { url, opcoes };
    return { status, ok: status >= 200 && status < 300, headers: { get: (k) => cabecalhos[k] || null }, json: async () => corpo };
  };

  resposta.atual = json(200, { cliente_ativo: true, mensagem: 'ativo!', tentar_em: null });
  assert.deepEqual(await lobby.perguntaEliteCasais('22122190000152'), { estado: 'ativo', mensagem: 'ativo!', tentarEm: null });
  assert.equal(pedido.url, '/api/casais/elite');
  assert.equal(pedido.opcoes.method, 'POST');
  assert.deepEqual(JSON.parse(pedido.opcoes.body), { cnpj: '22122190000152' });

  resposta.atual = json(200, { cliente_ativo: false, mensagem: 'não' });
  assert.equal((await lobby.perguntaEliteCasais('x')).estado, 'inativo');

  resposta.atual = json(200, { cliente_ativo: null, mensagem: 'fora', tentar_em: 60 });
  assert.deepEqual(await lobby.perguntaEliteCasais('x'), { estado: 'sem_resposta', mensagem: 'fora', tentarEm: 60 });

  resposta.atual = json(429, { erro: 'muito' }, { 'Retry-After': '120' });
  const limite = await lobby.perguntaEliteCasais('x');
  assert.deepEqual([limite.estado, limite.tentarEm], ['sem_resposta', 120]);
  assert.match(limite.mensagem, /preço cheio, R\$\s39\.997,00/);

  resposta.atual = json(502, null);
  assert.equal((await lobby.perguntaEliteCasais('x')).estado, 'sem_resposta');

  resposta.atual = async () => { throw new TypeError('Failed to fetch'); };
  assert.equal((await lobby.perguntaEliteCasais('x')).estado, 'sem_resposta');

  // A sessão caiu: sobe, e quem cuida é o login.
  resposta.atual = async () => { const e = new Error('caiu'); e.sessaoDerrubada = true; throw e; };
  await assert.rejects(lobby.perguntaEliteCasais('x'), /caiu/);
});
