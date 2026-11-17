"""
Conecta de mentira para o e2e do sinal de presença do Lobby (TSK-1520). Só a
biblioteca padrão; escuta só em 127.0.0.1.

Responde ao que o cliente 1.6.0 pede no caminho do e2e:

- `POST /api/v1/credenciais/verificar`: aceita a senha `senha-do-e2e`, para
  qualquer e-mail, com a identidade num UUID fixo por e-mail e sem papel (o
  consultor de todo dia);
- `GET /api/v1/identidades/<id>`: ativa (a revalidação da sessão do JWT);
- `POST /api/v1/presenca`: grava o sinal NA MEMÓRIA deste processo e responde
  204 — ou 503, depois de `POST /_falhar` (o Conecta fora, para o teste ver que
  a tela nem percebe).

E portas para o teste olhar o que chegou, que o Conecta de verdade não tem:

- `GET /_sinais` (JSON), `POST /_sinais/limpar`, `POST /_falhar` e `POST /_voltar`;
- `GET /_painel`: uma página no jeito do "Uso em tempo real", com o estado de
  agora de cada pessoa (o último sinal) e a lista do que chegou. É dela o print
  que mostra a pessoa no sistema Lobby, na tela certa.

Uso: python conecta_falso.py <porta>.
"""
import html
import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SENHA = "senha-do-e2e"
SISTEMAS = {"lobby": "Lobby"}

_trava = threading.Lock()
_sinais = []
_pessoas = {}  # identidade_id -> e-mail
_estado = {"falhando": False}


def _identidade(email):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"e2e-lobby:{email}"))


def _hora(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "—"


def _painel():
    with _trava:
        sinais = list(_sinais)
        pessoas = dict(_pessoas)
    agora = {}
    for sinal in sinais:
        agora[sinal["corpo"].get("identidade_id")] = sinal
    linhas = "".join(
        "<tr><td>{pessoa}</td><td>{sistema}</td><td><b>{modulo}</b> · {tela}</td>"
        "<td>{sinal}</td><td>{interacao}</td></tr>".format(
            pessoa=html.escape(pessoas.get(ident, ident or "?")),
            sistema=html.escape(s["sistema"]),
            modulo=html.escape(s["corpo"].get("modulo", "")),
            tela=html.escape(s["corpo"].get("tela", "")),
            sinal=_hora(s["recebido_em"]),
            interacao=_hora(s["corpo"].get("ultima_interacao")),
        )
        for ident, s in agora.items()
    ) or '<tr><td colspan="5" class="vazio">Ninguém mandou sinal.</td></tr>'
    historico = "".join(
        "<li><code>{hora}</code> {modulo} · {tela}</li>".format(
            hora=_hora(s["recebido_em"]),
            modulo=html.escape(s["corpo"].get("modulo", "")),
            tela=html.escape(s["corpo"].get("tela", "")),
        )
        for s in reversed(sinais)
    ) or "<li>nenhum</li>"
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Uso em tempo real (Conecta falso)</title><style>
body{{font:14px system-ui,sans-serif;background:#141412;color:#eee;margin:32px}}
h1{{font-size:20px;margin:0 0 4px}} p{{color:#aaa;margin:0 0 20px}}
table{{border-collapse:collapse;width:100%;margin-bottom:24px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid #333}}
th{{color:#c7a444;font-size:12px;text-transform:uppercase;letter-spacing:.06em}}
.vazio{{color:#888;font-style:italic}} code{{color:#c7a444}} li{{margin:2px 0}}
</style></head><body>
<h1>Uso em tempo real <small>(Conecta falso do e2e, TSK-1520)</small></h1>
<p>{len(sinais)} sinal(is) recebido(s) em <code>POST /api/v1/presenca</code>.</p>
<table><thead><tr><th>Pessoa</th><th>Sistema</th><th>Módulo · tela</th>
<th>Último sinal</th><th>Última interação</th></tr></thead><tbody>{linhas}</tbody></table>
<h2 style="font-size:15px">Sinais, do mais novo ao mais velho</h2><ul>{historico}</ul>
</body></html>"""


class Atendente(BaseHTTPRequestHandler):
    def _corpo(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        bruto = self.rfile.read(tamanho) if tamanho else b""
        return json.loads(bruto) if bruto else {}

    def _responder(self, status, corpo=None, tipo="application/json"):
        dados = b"" if corpo is None else (
            corpo.encode() if isinstance(corpo, str) else json.dumps(corpo).encode()
        )
        self.send_response(status)
        if dados:
            self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self):
        if self.path == "/api/v1/credenciais/verificar":
            corpo = self._corpo()
            email = str(corpo.get("login", "")).strip().lower()
            if corpo.get("senha") != SENHA:
                return self._responder(401, {"erro": "credencial_invalida"})
            ident = _identidade(email)
            with _trava:
                _pessoas[ident] = email
            return self._responder(200, {
                "identidade_id": ident,
                "email": email,
                "nome": "Carla do E2E",
                "precisa_trocar_senha": False,
                "config_do_app": {},
            })
        if self.path == "/api/v1/presenca":
            chave = self.headers.get("Authorization", "")
            slug = chave.removeprefix("AppKey ").split(":")[0]
            corpo = self._corpo()
            with _trava:
                if _estado["falhando"]:
                    falhou = True
                else:
                    falhou = False
                    _sinais.append({
                        "recebido_em": datetime.now(timezone.utc).isoformat(),
                        "sistema": SISTEMAS.get(slug, slug),
                        "com_chave": chave.startswith("AppKey "),
                        "pessoa": _pessoas.get(corpo.get("identidade_id")),
                        "corpo": corpo,
                    })
            if falhou:
                return self._responder(503, {"erro": "indisponivel"})
            return self._responder(204)
        if self.path == "/_sinais/limpar":
            with _trava:
                _sinais.clear()
            return self._responder(204)
        if self.path in ("/_falhar", "/_voltar"):
            with _trava:
                _estado["falhando"] = self.path == "/_falhar"
            return self._responder(204)
        return self._responder(404, {"erro": "nao_encontrado"})

    def do_GET(self):
        if self.path.startswith("/api/v1/identidades/"):
            ident = self.path.rsplit("/", 1)[-1]
            return self._responder(200, {"identidade_id": ident, "ativo": True})
        if self.path == "/_sinais":
            with _trava:
                return self._responder(200, list(_sinais))
        if self.path == "/_painel":
            return self._responder(200, _painel(), tipo="text/html")
        return self._responder(404, {"erro": "nao_encontrado"})

    def log_message(self, formato, *args):
        sys.stderr.write("conecta-falso: " + formato % args + "\n")


if __name__ == "__main__":
    porta = int(sys.argv[1])
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Atendente)
    print(f"conecta-falso em http://127.0.0.1:{porta}", flush=True)
    servidor.serve_forever()
