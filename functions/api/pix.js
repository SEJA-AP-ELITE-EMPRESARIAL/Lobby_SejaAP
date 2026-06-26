// Cloudflare Pages Function — proxy seguro para o webhook Pix do n8n.
//
// O navegador chama /api/pix (mesma origem, SEM chave). Esta função injeta o
// header de autenticação `easyflow-key` a partir de um SEGREDO do projeto Pages
// (variável de ambiente EASYFLOW_KEY) e repassa a requisição ao n8n. Assim a
// chave nunca chega ao navegador.
//
// Configure o segredo no painel:
//   Cloudflare > Pages > (projeto) > Settings > Variables and secrets
//   > Add > tipo "Secret" > Name: EASYFLOW_KEY > Value: <a chave>

const N8N_PIX_URL = 'https://n8n.sejaap.com.br/webhook/907bbfb8-b366-4f1e-9871-9cc4ba98c5e8';

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

export async function onRequestPost(context) {
  const key = context.env && context.env.EASYFLOW_KEY;
  if (!key) {
    return json({ success: false, error: 'EASYFLOW_KEY não configurada no Pages' }, 500);
  }

  let body = '';
  try { body = await context.request.text(); } catch (e) { body = ''; }

  let resp;
  try {
    resp = await fetch(N8N_PIX_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'accept': 'application/json',
        'easyflow-key': key,
      },
      body,
    });
  } catch (e) {
    return json({ success: false, error: 'falha ao contatar o n8n' }, 502);
  }

  // Repassa a resposta do n8n como veio (status + corpo).
  const text = await resp.text();
  return new Response(text, {
    status: resp.status,
    headers: { 'content-type': resp.headers.get('content-type') || 'application/json; charset=utf-8' },
  });
}

// Mesma origem não dispara preflight, mas respondemos OPTIONS por segurança.
export async function onRequestOptions() {
  return new Response(null, { status: 204 });
}
