# Front do Lobby — nginx servindo dois arquivos HTML.
#
# O contexto de build é a RAIZ do repositório, não esta pasta: `index.html` e
# `admin.html` continuam onde sempre estiveram. Isso é de propósito — o projeto
# na Cloudflare Pages publica a raiz, e ele fica de pé como rollback até a
# migração para a VPS estabilizar. Mover os arquivos quebraria o plano B.
FROM nginx:1.27-alpine

COPY deploy/frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Só isto. Não há bundle, não há node_modules, não há assets locais: o React e o
# Babel vêm de CDN e o SVG do logo está inline no próprio HTML.
COPY index.html /usr/share/nginx/html/index.html
COPY admin.html /usr/share/nginx/html/admin.html

EXPOSE 80
