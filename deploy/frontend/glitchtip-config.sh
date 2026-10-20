#!/bin/sh
# Escreve o /glitchtip/config.js que os HTMLs do Lobby leem antes de ligar o SDK
# do GlitchTip no navegador (TSK-1142). Roda na subida do container, pelo
# /docker-entrypoint.d/ da imagem do nginx, antes de o nginx abrir a porta.
#
#   SENTRY_DSN          o DSN do projeto `lobby` (o mesmo do backend), do .env.
#                       Vazio, ou fora do formato de DSN = SDK desligado.
#   SENTRY_ENVIRONMENT  'production' se vazio.
#   GIT_COMMIT          o release; gravado na imagem pelo build.
#
# O DSN de front é público por natureza (vai para o navegador de qualquer um).
# O que este script garante é que nada além de um DSN, um commit e um nome de
# ambiente vira JavaScript: cada valor passa por uma lista fechada de caracteres,
# então aspa, barra invertida ou </script> não chegam ao arquivo.
#
# Nunca falha: um GlitchTip mal configurado não pode impedir o nginx de subir.

# O teste (tests/front/glitchtip.test.mjs) troca o destino; o container, nunca.
destino=${GLITCHTIP_CONFIG_JS:-/usr/share/nginx/html/glitchtip/config.js}

dsn=$(printf '%s' "${SENTRY_DSN:-}" | tr -d ' \t\r\n')
release=$(printf '%s' "${GIT_COMMIT:-}" | tr -d ' \t\r\n')
ambiente=$(printf '%s' "${SENTRY_ENVIRONMENT:-}" | tr -d ' \t\r\n')

if [ -n "$dsn" ] && ! printf '%s' "$dsn" | grep -Eq '^https?://[A-Za-z0-9]{1,64}@[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?(/[A-Za-z0-9._-]{1,64})*/[0-9]{1,12}$'; then
    echo "[GLITCHTIP] front desligado: SENTRY_DSN fora do formato de DSN"
    dsn=""
fi
if [ -n "$release" ] && ! printf '%s' "$release" | grep -Eq '^[A-Za-z0-9._-]{1,64}$'; then
    echo "[GLITCHTIP] GIT_COMMIT fora do formato: o front segue sem release"
    release=""
fi
if ! printf '%s' "$ambiente" | grep -Eq '^[A-Za-z0-9_-]{1,32}$'; then
    ambiente=production
fi

mkdir -p "$(dirname "$destino")"
printf 'window.LOBBY_GLITCHTIP = {"dsn": "%s", "release": "%s", "environment": "%s"};\n' \
    "$dsn" "$release" "$ambiente" > "$destino" || {
    echo "[GLITCHTIP] front desligado: não deu para escrever $destino"
    exit 0
}

if [ -n "$dsn" ]; then
    echo "[GLITCHTIP] front ligado (release=${release:--})"
else
    echo "[GLITCHTIP] front desligado: sem SENTRY_DSN"
fi
exit 0
