/* ============================================================================
 * Catálogo — normalização, validação e semente (seed).
 *
 * O catálogo vive no KV (namespace CATALOGO, chave "atual"). Este módulo é a
 * única fonte de verdade sobre o FORMATO dele, usada tanto na leitura quanto
 * na gravação, para que um PUT nunca grave algo que o lobby não saiba ler.
 *
 * CATALOGO_PADRAO é a semente: se o KV estiver vazio (primeiro deploy), o GET
 * responde com ela. É uma cópia fiel dos valores que estavam no index.html.
 * ========================================================================== */

export const CHAVE_KV = 'atual';

export const CATALOGO_PADRAO = [
  {
    id: 'elite', name: 'Elite', icon: 'diamond', color: 'purple',
    desc: 'Consultoria individual contínua — os planos Elite da Seja AP.',
    products: [
      { id: 'prep',   name: 'ELITE PREPARAÇÃO', sigla: 'PRE', desc: 'O plano do Início',    duration: '12 meses', monthly: 9997,  price: 119964, recurring: true, vigencia: 12, icon: 'rocket_launch' },
      { id: 'pro',    name: 'ELITE PRO',        sigla: 'PRO', desc: 'O plano da Estrutura', duration: '12 meses', monthly: 12997, price: 155964, recurring: true, vigencia: 12, icon: 'workspace_premium' },
      { id: 'gestao', name: 'ELITE GESTÃO',     sigla: 'GES', desc: 'O plano da Escala',    duration: '12 meses', monthly: 19997, price: 239964, recurring: true, vigencia: 12, icon: 'insights' },
      { id: 'evo',    name: 'ELITE EVO',        sigla: 'EVO', desc: 'O plano do Legado',    duration: '12 meses', monthly: 29997, price: 359964, recurring: true, vigencia: 12, icon: 'diamond' },
    ],
  },
  {
    id: 'treinamentos', name: 'Treinamentos', icon: 'school', color: 'gold', locked: true,
    desc: 'Imersões e trilhas de desenvolvimento de líderes e equipes comerciais.',
    products: [
      { id: 't1', name: 'Imersão Gestão 360',         sigla: 'IMG', desc: 'Imersão presencial de 3 dias',   duration: '3 dias',  price: 18000, icon: 'groups' },
      { id: 't2', name: 'Trilha Líder Elite',         sigla: 'TLE', desc: 'Programa de formação de líderes', duration: '6 meses', price: 24000, icon: 'military_tech' },
      { id: 't3', name: 'Vendas de Alta Performance', sigla: 'VAP', desc: 'Treinamento intensivo de vendas', duration: '2 meses', price: 12000, icon: 'trending_up' },
    ],
  },
  {
    id: 'palestras', name: 'Palestras', icon: 'campaign', color: 'green', locked: true,
    desc: 'Palestras in company e eventos de alto impacto.',
    products: [
      { id: 'pl1', name: 'Palestra In Company',      sigla: 'PIC', desc: 'Palestra exclusiva na empresa',  duration: '1 evento', price: 15000, icon: 'record_voice_over' },
      { id: 'pl2', name: 'Palestra Magna',           sigla: 'PMA', desc: 'Palestra para grandes públicos', duration: '1 evento', price: 25000, icon: 'stadium' },
      { id: 'pl3', name: 'Ciclo de Palestras Anual', sigla: 'CPA', desc: 'Programa anual de palestras',    duration: '12 meses', price: 80000, icon: 'event' },
    ],
  },
];

/* ============================================================================
 * CATEGORIAS DE FLUXO PRÓPRIO — definidas em código, fora do KV.
 *
 * A APN não tem produtos nem valor de tabela: o consultor digita o valor da
 * venda. Como não há nada para a diretoria editar em /admin, ela não passa pelo
 * KV — mora aqui e é reinserida na leitura do catálogo.
 *
 * Isso resolve um problema concreto: o catálogo do KV já gravado em produção
 * traz a APN travada e com 3 produtos antigos. Se a APN dependesse do KV, este
 * deploy não a destravaria — seria preciso republicar o catálogo pelo /admin.
 * Do jeito que está, destravar ou mudar a APN é só deploy.
 *
 * `flow: 'apn'` é o que o lobby lê para escolher o fluxo enxuto (CNPJ + contato
 * + valor + forma de pagamento + origem) em vez do wizard completo do Elite.
 * ========================================================================== */
export const CATEGORIA_APN = {
  id: 'apn',
  name: 'APN',
  icon: 'rocket_launch',
  color: 'blue',
  flow: 'apn',
  sigla: 'APN',   // alimenta o protocolo da venda (SSS-YYMMDDPRRRRR)
  desc: 'Aceleração de Performance de Negócios — valor definido na negociação.',
  products: [],
};

// Posição da APN no lobby (era a 3ª categoria, depois de Treinamentos).
const POSICAO_APN = 2;

/** Tira as categorias de fluxo próprio — elas nunca são gravadas no KV. */
function semCategoriasDeCodigo(cats) {
  return (cats || []).filter((c) => c?.id !== CATEGORIA_APN.id);
}

/** Recoloca as categorias de fluxo próprio na posição em que aparecem no lobby. */
export function comCategoriasDeCodigo(cats) {
  const out = semCategoriasDeCodigo(cats);
  out.splice(Math.min(POSICAO_APN, out.length), 0, CATEGORIA_APN);
  return out;
}

const CORES = ['gold', 'blue', 'green', 'purple'];
const num = (v) => (typeof v === 'number' && isFinite(v) ? v : Number(String(v ?? '').replace(',', '.')));
const round2 = (n) => Math.round((Number(n) + Number.EPSILON) * 100) / 100;

/**
 * Valida e normaliza o catálogo vindo do admin.
 * Devolve { cats } ou { erro } — nunca grava algo pela metade.
 *
 * A regra que importa: para produto recorrente, `price` é SEMPRE derivado
 * (monthly × vigencia). O admin só edita a mensalidade; o total nunca é
 * digitado à mão, então não há como os dois discordarem.
 *
 * Categorias de fluxo próprio (APN) são descartadas aqui: elas vivem em código,
 * não no KV. O admin devolve o catálogo inteiro no PUT — inclusive a APN, que
 * ele recebeu no GET — e é este filtro que impede que ela seja gravada.
 */
export function normalizaCatalogo(entradaBruta) {
  const entrada = semCategoriasDeCodigo(entradaBruta);
  if (!Array.isArray(entradaBruta) || entrada.length === 0) {
    return { erro: 'O catálogo precisa ser uma lista com ao menos uma categoria.' };
  }
  const cats = [];
  const idsCat = new Set();

  for (const c of entrada) {
    if (!c?.id || !c?.name) return { erro: `Categoria sem id ou nome: ${JSON.stringify(c?.id ?? c)}` };
    if (idsCat.has(c.id)) return { erro: `Categoria duplicada: "${c.id}".` };
    idsCat.add(c.id);
    if (!Array.isArray(c.products) || c.products.length === 0) {
      return { erro: `Categoria "${c.name}" está sem produtos.` };
    }

    const products = [];
    const idsProd = new Set();

    for (const p of c.products) {
      if (!p?.id || !p?.name) return { erro: `Produto sem id ou nome na categoria "${c.name}".` };
      if (idsProd.has(p.id)) return { erro: `Produto duplicado "${p.id}" na categoria "${c.name}".` };
      idsProd.add(p.id);

      const recurring = !!p.recurring;
      const vigencia = recurring ? Math.trunc(num(p.vigencia) || 12) : undefined;
      if (recurring && (!vigencia || vigencia < 1 || vigencia > 120)) {
        return { erro: `Vigência inválida em "${p.name}": use de 1 a 120 meses.` };
      }

      const monthly = recurring ? round2(num(p.monthly)) : undefined;
      if (recurring && (!(monthly > 0) || monthly > 10_000_000)) {
        return { erro: `Mensalidade inválida em "${p.name}".` };
      }

      // Recorrente: total derivado. À vista: total é o que foi digitado.
      const price = recurring ? round2(monthly * vigencia) : round2(num(p.price));
      if (!(price > 0) || price > 100_000_000) {
        return { erro: `Valor inválido em "${p.name}".` };
      }

      // `sigla` alimenta o protocolo da venda (SSS-YYMMDDPRRRRR) — 3 letras, sempre.
      const sigla = String(p.sigla || p.name.slice(0, 3)).toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3).padEnd(3, 'X');

      products.push({
        id: String(p.id), name: String(p.name), sigla,
        desc: String(p.desc ?? ''), duration: String(p.duration ?? ''),
        icon: String(p.icon || 'workspace_premium'),
        price,
        ...(recurring ? { monthly, recurring: true, vigencia } : {}),
      });
    }

    cats.push({
      id: String(c.id), name: String(c.name),
      icon: String(c.icon || 'diamond'),
      color: CORES.includes(c.color) ? c.color : 'gold',
      desc: String(c.desc ?? ''),
      ...(c.locked ? { locked: true } : {}),
      products,
    });
  }
  return { cats };
}

/**
 * Lê o catálogo do KV; cai na semente se ainda não houver nada gravado.
 * Em qualquer um dos casos as categorias de fluxo próprio (APN) são reinseridas
 * a partir do código — o que estiver gravado no KV para elas é ignorado.
 */
export async function leCatalogo(env) {
  const bruto = await env.CATALOGO.get(CHAVE_KV, 'json');
  if (!bruto?.cats?.length) {
    return { cats: comCategoriasDeCodigo(CATALOGO_PADRAO), atualizadoEm: null, origem: 'padrao' };
  }
  return { cats: comCategoriasDeCodigo(bruto.cats), atualizadoEm: bruto.atualizadoEm ?? null, origem: 'kv' };
}
