#!/usr/bin/env bash
# E2E do sinal de presença (TSK-1520). O roteiro de subir e derrubar a pilha é o
# `rodar-e2e.sh`; este roda só o `presenca.spec.mjs`.
#
#   tests/e2e/rodar-presenca.sh          # PYTHON=… para outro interpretador
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/rodar-e2e.sh" presenca.spec.mjs "$@"
