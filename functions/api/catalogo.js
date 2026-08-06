/* ============================================================================
 * GET  /api/catalogo  ->  { cats, atualizadoEm, origem }   (público — o lobby lê)
 * PUT  /api/catalogo  ->  grava o catálogo no KV           (exige token da diretoria)
 *
 * O GET é público de propósito: o lobby precisa dos preços para funcionar, e eles
 * já são visíveis na tela para qualquer consultor. O que é restrito é ESCREVER.
 * ========================================================================== */
import { exigeDiretoria, json } from '../_lib/auth.js';
import { CHAVE_KV, comCategoriasDeCodigo, leCatalogo, normalizaCatalogo } from '../_lib/catalogo.js';

export async function onRequestGet({ env }) {
  const dados = await leCatalogo(env);
  return json(dados);
}

export async function onRequestPut({ request, env }) {
  const negado = await exigeDiretoria(request, env);
  if (negado) return negado;

  let corpo;
  try {
    corpo = await request.json();
  } catch {
    return json({ erro: 'JSON inválido.' }, 400);
  }

  const { cats, erro } = normalizaCatalogo(corpo?.cats);
  if (erro) return json({ erro }, 400);

  const registro = { cats, atualizadoEm: new Date().toISOString() };
  await env.CATALOGO.put(CHAVE_KV, JSON.stringify(registro));

  // Guarda a versão anterior sob um carimbo de data — se um valor for salvo
  // errado no meio de um evento, dá para ver o que havia antes.
  await env.CATALOGO.put(`historico:${registro.atualizadoEm}`, JSON.stringify(registro), {
    expirationTtl: 90 * 24 * 60 * 60, // 90 dias
  });

  // Devolve o catálogo como o lobby vai vê-lo (com as categorias de fluxo
  // próprio), para o admin não "perder" a APN da tela depois de publicar.
  return json({ ok: true, atualizadoEm: registro.atualizadoEm, cats: comCategoriasDeCodigo(cats) });
}
