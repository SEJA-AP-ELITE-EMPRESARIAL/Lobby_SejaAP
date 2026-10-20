# Front do Lobby — nginx servindo três arquivos HTML (e o SDK do GlitchTip).
#
# O contexto de build é a RAIZ do repositório, não esta pasta: os HTMLs
# continuam onde sempre estiveram. Isso é de propósito — o projeto
# na Cloudflare Pages publica a raiz, e ele fica de pé como rollback até a
# migração para a VPS estabilizar. Mover os arquivos quebraria o plano B.
FROM nginx:1.27-alpine

COPY deploy/frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Só isto. Não há bundle, não há node_modules: o React e o Babel vêm de CDN e o
# SVG do logo está inline no próprio HTML.
COPY index.html /usr/share/nginx/html/index.html
COPY definir-senha.html /usr/share/nginx/html/definir-senha.html
COPY esqueci-senha.html /usr/share/nginx/html/esqueci-senha.html

# Erros do navegador no GlitchTip (TSK-1142): o SDK, com versão fixa e o arquivo
# no repositório (o sha256 é conferido em tests/front/glitchtip.test.mjs), e a
# limpeza do evento. Servidos daqui, e não de CDN: um CDN fora do ar não pode
# segurar o carregamento do lobby. O `config.js` com o DSN é escrito na subida,
# pelo script abaixo, a partir do SENTRY_DSN do ambiente.
COPY glitchtip/lobby.js glitchtip/sentry-browser-11.8.0.min.js /usr/share/nginx/html/glitchtip/
COPY deploy/frontend/glitchtip-config.sh /docker-entrypoint.d/40-glitchtip-config.sh
RUN chmod 755 /docker-entrypoint.d/40-glitchtip-config.sh

# O commit desta imagem, que vira o `release` dos erros do navegador. Vem do
# docker-compose.yml, como o do backend. No fim, para não invalidar o cache.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

EXPOSE 80
