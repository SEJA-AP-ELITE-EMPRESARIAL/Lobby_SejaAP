#!/usr/bin/env bash
# E2E do Lobby: a tela real (o index.html no Chromium), o backend local (Django
# com SQLite descartável) e um Conecta falso no lugar do de verdade. Nasceu com o
# sinal de presença (TSK-1520); o Casais de Alta Performance (TSK-1622) roda aqui
# também.
#
#   tests/e2e/rodar-e2e.sh                     # todos os *.spec.mjs
#   tests/e2e/rodar-e2e.sh casais.spec.mjs     # um só (o rodar-presenca.sh é isto)
#   PYTHON=…                                   # outro interpretador
#
# Na primeira vez: `npm ci` nesta pasta e `npx playwright install chromium`.
# Sobe os três em 127.0.0.1, roda os specs e derruba tudo ao sair, com sucesso ou
# não. Os prints vão para tests/e2e/prints-da-rodada/ (fora do git);
# `E2E_PRINTS=tests/e2e/prints` renova os de referência. Nada aqui fala com
# produção: ver apoio/settings_e2e.py (e, no Casais, os webhooks do n8n são
# respondidos pelo próprio teste). O index.html puxa React e Babel da CDN
# (unpkg), como em produção: sem internet, a tela não abre.
set -euo pipefail

E2E="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$E2E/../.." && pwd)"
BACK="$RAIZ/backend"
PY="${PYTHON:-$BACK/.venv/bin/python}"
PORTA_CONECTA="${PORTA_CONECTA:-41561}"
PORTA_BACK="${PORTA_BACK:-41562}"
PORTA_FRONT="${PORTA_FRONT:-41563}"

TMP="$(mktemp -d)"
PIDS=()
encerrar() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -rf "$TMP"
}
trap encerrar EXIT

# A porta tem de estar livre ANTES de subir cada servidor: ocupada (outra obra
# rodando um e2e), o servidor novo morreria logo, mas a espera abaixo seria
# respondida pelo da outra, e a rodada seguiria contra a pilha errada.
livre() {  # livre <porta> <nome>
  if (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; then
    echo "$2: a porta $1 já está ocupada (outra obra rodando o e2e?)." >&2
    exit 1
  fi
}

# esperar <url> <nome> <log> <pid>: até o servidor responder, ou o processo que
# este roteiro subiu morrer.
esperar() {
  for _ in $(seq 1 120); do
    if ! kill -0 "$4" 2>/dev/null; then
      echo "$2 morreu ao subir; log:" >&2
      cat "$3" >&2
      return 1
    fi
    curl -s -o /dev/null "$1" && return 0
    sleep 0.5
  done
  echo "$2 não subiu; log:" >&2
  cat "$3" >&2
  return 1
}

export E2E_CONECTA="http://127.0.0.1:$PORTA_CONECTA"
export E2E_BANCO="$TMP/db.sqlite3"
export DJANGO_SETTINGS_MODULE=settings_e2e
export PYTHONPATH="$E2E/apoio${PYTHONPATH:+:$PYTHONPATH}"

livre "$PORTA_CONECTA" "Conecta falso"
"$PY" "$E2E/apoio/conecta_falso.py" "$PORTA_CONECTA" >"$TMP/conecta.log" 2>&1 &
PIDS+=($!)
esperar "$E2E_CONECTA/_sinais" "Conecta falso" "$TMP/conecta.log" "$!"

# O banco nasce com o catálogo (as migrations o semeiam).
(cd "$BACK" && "$PY" manage.py migrate --noinput -v 0)
livre "$PORTA_BACK" "Backend"
(cd "$BACK" && exec "$PY" manage.py runserver "127.0.0.1:$PORTA_BACK" --noreload) >"$TMP/backend.log" 2>&1 &
PIDS+=($!)
esperar "http://127.0.0.1:$PORTA_BACK/saude" "Backend" "$TMP/backend.log" "$!"

livre "$PORTA_FRONT" "Front"
"$PY" "$E2E/apoio/servidor_do_front.py" "$PORTA_FRONT" "http://127.0.0.1:$PORTA_BACK" >"$TMP/front.log" 2>&1 &
PIDS+=($!)
esperar "http://127.0.0.1:$PORTA_FRONT/" "Front" "$TMP/front.log" "$!"

status=0
E2E_URL="http://127.0.0.1:$PORTA_FRONT" \
  node "$E2E/node_modules/@playwright/test/cli.js" test --config "$E2E/playwright.config.mjs" "$@" || status=$?
if [ "$status" -ne 0 ]; then
  echo "--- backend.log (fim) ---" >&2; tail -40 "$TMP/backend.log" >&2
fi
exit "$status"
