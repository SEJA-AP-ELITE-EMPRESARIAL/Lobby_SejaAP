/* ============================================================================
 * POST /api/sessao   { senha }  ->  { token, exp }
 *
 * Único lugar onde a senha da diretoria é conferida. Ela vive como secret no
 * painel do Cloudflare (env.SENHA_DIRETORIA) — não está no código, não está no
 * git, e não aparece no "ver código-fonte" da página.
 *
 * Como a senha é única e compartilhada, um atacante só precisaria adivinhar uma
 * string — então limitamos a taxa de tentativas por IP.
 * ========================================================================== */
import { comparaSegura, emiteToken, json } from '../_lib/auth.js';

const MAX_TENTATIVAS = 10;
const JANELA_S = 15 * 60;

export async function onRequestPost({ request, env }) {
  if (!env.SENHA_DIRETORIA || !env.TOKEN_SECRET) {
    return json({ erro: 'Servidor sem SENHA_DIRETORIA/TOKEN_SECRET configurados.' }, 500);
  }

  const ip = request.headers.get('CF-Connecting-IP') || 'desconhecido';
  const chaveRl = `rl:sessao:${ip}`;
  const tentativas = Number(await env.CATALOGO.get(chaveRl)) || 0;
  if (tentativas >= MAX_TENTATIVAS) {
    return json({ erro: 'Muitas tentativas. Tente de novo em 15 minutos.' }, 429);
  }

  let senha;
  try {
    ({ senha } = await request.json());
  } catch {
    return json({ erro: 'Requisição inválida.' }, 400);
  }

  if (!senha || !comparaSegura(senha, env.SENHA_DIRETORIA)) {
    // Só erros contam para o limite — quem acerta não é penalizado.
    await env.CATALOGO.put(chaveRl, String(tentativas + 1), { expirationTtl: JANELA_S });
    return json({ erro: 'Senha incorreta.' }, 401);
  }

  await env.CATALOGO.delete(chaveRl);
  const { token, exp } = await emiteToken(env.TOKEN_SECRET);
  return json({ token, exp });
}
