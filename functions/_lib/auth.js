/* ============================================================================
 * Token de sessão da diretoria — HMAC-SHA256 via WebCrypto (nativo do Workers).
 *
 * A senha NUNCA sai do servidor. O navegador manda a senha uma vez em
 * POST /api/sessao; se confere, recebe de volta um token assinado e com prazo.
 * Esse token é o que destrava a edição de valores no lobby e no /admin.
 *
 * Formato: base64url(payload_json).base64url(assinatura)
 * Sem dependência externa — Workers já expõe crypto.subtle.
 * ========================================================================== */

const TTL_PADRAO_S = 8 * 60 * 60; // 8h — cobre um dia de trabalho sem re-login.

const enc = new TextEncoder();

const b64urlEncode = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

const b64urlDecode = (str) => {
  const pad = str.replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(pad + '==='.slice((pad.length + 3) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
};

async function chave(secret) {
  return crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify'],
  );
}

/**
 * Compara dois strings em tempo constante. Uma comparação normal (===) vaza,
 * pelo tempo de resposta, quantos caracteres iniciais da senha estão certos.
 */
export function comparaSegura(a, b) {
  const ba = enc.encode(String(a));
  const bb = enc.encode(String(b));
  // Tamanhos diferentes já reprovam, mas ainda percorremos tudo para não vazar o tamanho.
  let diff = ba.length ^ bb.length;
  for (let i = 0; i < Math.max(ba.length, bb.length); i++) {
    diff |= (ba[i] ?? 0) ^ (bb[i] ?? 0);
  }
  return diff === 0;
}

/** Assina um token de diretoria válido por `ttlS` segundos. */
export async function emiteToken(secret, ttlS = TTL_PADRAO_S) {
  const payload = { papel: 'diretoria', exp: Math.floor(Date.now() / 1000) + ttlS };
  const corpo = b64urlEncode(enc.encode(JSON.stringify(payload)));
  const sig = await crypto.subtle.sign('HMAC', await chave(secret), enc.encode(corpo));
  return { token: `${corpo}.${b64urlEncode(sig)}`, exp: payload.exp };
}

/**
 * Verifica assinatura e prazo. Devolve o payload, ou null se inválido/expirado.
 * Um token adulterado no navegador falha aqui — a assinatura não fecha sem o secret.
 */
export async function verificaToken(secret, token) {
  if (typeof token !== 'string' || !token.includes('.')) return null;
  const [corpo, sig] = token.split('.');
  if (!corpo || !sig) return null;
  let ok;
  try {
    ok = await crypto.subtle.verify('HMAC', await chave(secret), b64urlDecode(sig), enc.encode(corpo));
  } catch {
    return null; // base64 malformado
  }
  if (!ok) return null;
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(b64urlDecode(corpo)));
  } catch {
    return null;
  }
  if (!payload?.exp || payload.exp < Math.floor(Date.now() / 1000)) return null;
  return payload;
}

/** Lê o token do header Authorization: Bearer <token>. */
export function tokenDoRequest(request) {
  const h = request.headers.get('Authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7).trim() : '';
}

/** Guarda de rota: devolve null se autorizado, ou uma Response 401/500 se não. */
export async function exigeDiretoria(request, env) {
  if (!env.TOKEN_SECRET) {
    return json({ erro: 'TOKEN_SECRET não configurado no Cloudflare.' }, 500);
  }
  const payload = await verificaToken(env.TOKEN_SECRET, tokenDoRequest(request));
  if (!payload) {
    return json({ erro: 'Sessão inválida ou expirada. Entre novamente com a senha da diretoria.' }, 401);
  }
  return null;
}

export function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}
