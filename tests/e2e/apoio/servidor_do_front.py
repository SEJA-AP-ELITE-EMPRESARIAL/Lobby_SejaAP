"""
O papel do nginx do container do Lobby no e2e (TSK-1520), só com a biblioteca
padrão e só em 127.0.0.1: serve os HTMLs da raiz do repositório e passa o
`/api/` ao backend local. Como o `deploy/frontend/nginx.conf`:

- `/` é o `index.html`; `/esqueci-senha` e `/definir-senha` são as telas de senha;
- `/glitchtip/config.js` sai vazio de DSN (o front não liga o GlitchTip), e o
  resto do `/glitchtip/` é o arquivo do repositório;
- `/api/...` vai ao backend com método, cabeçalhos e corpo, e a resposta volta
  como veio (inclusive 4xx e 5xx).

Uso: python servidor_do_front.py <porta> <url do backend>.
"""
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
PAGINAS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/esqueci-senha": "esqueci-senha.html",
    "/definir-senha": "definir-senha.html",
}
CONFIG_DO_GLITCHTIP = b'window.LOBBY_GLITCHTIP = {"dsn": "", "release": "", "environment": "e2e"};\n'
SEM_REPASSE = {"host", "connection", "content-length", "accept-encoding"}


class Front(BaseHTTPRequestHandler):
    backend = ""

    def _arquivo(self, caminho):
        if caminho == "/glitchtip/config.js":
            return self._responder(200, CONFIG_DO_GLITCHTIP, "text/javascript")
        nome = PAGINAS.get(caminho)
        if nome:
            return self._responder(200, (RAIZ / nome).read_bytes(), "text/html; charset=utf-8")
        if caminho.startswith("/glitchtip/") and caminho.endswith(".js") and "/.." not in caminho:
            arquivo = RAIZ / caminho.lstrip("/")
            if arquivo.is_file():
                return self._responder(200, arquivo.read_bytes(), "text/javascript")
        return self._responder(404, b"nao encontrado", "text/plain")

    def _responder(self, status, corpo, tipo, extras=()):
        self.send_response(status)
        if corpo or status != 204:
            self.send_header("Content-Type", tipo)
        for nome, valor in extras:
            self.send_header(nome, valor)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if corpo:
            self.wfile.write(corpo)

    def _api(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else None
        pedido = urllib.request.Request(
            self.backend + self.path, data=corpo, method=self.command,
            headers={k: v for k, v in self.headers.items() if k.lower() not in SEM_REPASSE},
        )
        try:
            with urllib.request.urlopen(pedido, timeout=30) as r:
                status, dados, cabecalhos = r.status, r.read(), r.headers
        except urllib.error.HTTPError as erro:
            status, dados, cabecalhos = erro.code, erro.read(), erro.headers
        except OSError:
            return self._responder(502, b'{"erro": "backend fora"}', "application/json")
        extras = [(k, v) for k, v in cabecalhos.items()
                  if k.lower() in {"www-authenticate", "retry-after"}]
        return self._responder(status, dados, cabecalhos.get("Content-Type", "application/json"), extras)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api()
        return self._arquivo(self.path.split("?", 1)[0])

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api()
        return self._responder(405, b"", "text/plain")

    do_PUT = do_PATCH = do_DELETE = do_POST

    def log_message(self, formato, *args):
        sys.stderr.write("front: " + formato % args + "\n")


if __name__ == "__main__":
    porta, Front.backend = int(sys.argv[1]), sys.argv[2].rstrip("/")
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Front)
    print(f"front em http://127.0.0.1:{porta} (api -> {Front.backend})", flush=True)
    servidor.serve_forever()
