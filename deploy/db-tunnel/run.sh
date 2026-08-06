#!/usr/bin/env sh
# Encaminha db-tunnel:5437 (rede do compose) -> 127.0.0.1:5437 do db-sejaap.
#
# 5437 é a próxima porta livre na db-sejaap: 5433 kanban, 5434 CRM,
# 5435 formulários, 5436 identidade.
set -e
: "${TUNNEL_HOST:?defina TUNNEL_HOST}"
: "${TUNNEL_USER:=root}"
: "${TUNNEL_PORT:=22}"
exec autossh -M 0 -N \
    -o "StrictHostKeyChecking=yes" \
    -o "UserKnownHostsFile=/keys/known_hosts" \
    -o "IdentitiesOnly=yes" \
    -o "ExitOnForwardFailure=yes" \
    -o "ServerAliveInterval=30" \
    -o "ServerAliveCountMax=3" \
    -i /keys/id \
    -p "${TUNNEL_PORT}" \
    -L "0.0.0.0:5437:127.0.0.1:5437" \
    "${TUNNEL_USER}@${TUNNEL_HOST}"
