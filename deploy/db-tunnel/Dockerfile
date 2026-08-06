# Túnel SSH até o Postgres do Lobby na db-sejaap.
#
# O banco não fica nesta VPS e a porta dele escuta em loopback no destino — o
# túnel é o único caminho. Mesmo desenho do CRM e do Formulários Financeiro.
FROM alpine:3.20

RUN apk add --no-cache autossh openssh-client

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
