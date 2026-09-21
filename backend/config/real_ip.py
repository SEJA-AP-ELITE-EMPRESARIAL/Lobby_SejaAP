"""
O IP de quem está do outro lado, e não o do último proxy (TSK-195).

O CAMINHO ATÉ AQUI, lido da configuração de produção em 21/09/2026:

    navegador → Cloudflare → nginx do host (TLS, .164) → docker-proxy
              → nginx do container `lobby-frontend` → gunicorn

O que cada header vale quando chega ao Django:

- `REMOTE_ADDR` é o IP do container do nginx (172.23.0.5). O mesmo para todo
  mundo.
- `X-Forwarded-For` chega como
  `<o que o cliente mandou>, <IP do cliente>, <borda da Cloudflare>, <gateway do docker>`,
  porque a Cloudflare e os dois nginx ANEXAM (`$proxy_add_x_forwarded_for`). A
  primeira posição é escolhida por quem faz a requisição: basta mandar o header.
- `X-Real-IP` é reescrito pelo nginx do container com o gateway do docker
  (172.23.0.1). O mesmo para todo mundo, por isso não entra aqui.
- `CF-Connecting-IP` é escrito pela Cloudflare e atravessa os dois nginx sem
  mudar. É o único que diz quem é o visitante sem que ele possa escolher.

O QUE ESTAVA ERRADO

Os throttles anônimos do DRF (`AnonRateThrottle`) rodavam sem `NUM_PROXIES`, e
nesse caso o DRF usa o `X-Forwarded-For` INTEIRO como chave do balde. Mandar um
header diferente a cada requisição zerava o limite. E o IP que ia para o Conecta
ID no login, e para o comprovante de venda, era o `X-Forwarded-For[0]`: o
atacante escolhia o IP que o bloqueio por origem via e que a trilha gravava.

A CORREÇÃO É A MESMA DO CONECTAAP (TSK-269, `middleware/security/real_ip.py`)

Este middleware reescreve `REMOTE_ADDR` com o `CF-Connecting-IP`, e só quando a
conexão veio de dentro da malha de proxies (endereço privado ou loopback). Com
`NUM_PROXIES = 0` no DRF, o throttle passa a usar esse `REMOTE_ADDR`. Não se
contam saltos no `X-Forwarded-For` porque a contagem quebra em silêncio quando
a topologia muda.

Ele também reescreve o `X-Forwarded-For` com o mesmo endereço. É o que protege o
`identidade_client.py`, que é cópia do Conecta ID e não se edita aqui: o
`BackendIdentidade._ip` de lá lê o `X-Forwarded-For[0]`, e depois deste
middleware essa posição passa a ser o IP real. Os originais ficam em
`REMOTE_ADDR_ORIGINAL` e `X_FORWARDED_FOR_ORIGINAL`, sem o prefixo `HTTP_` para
não voltarem a parecer header.

LIMITE CONHECIDO, QUE SÓ SE FECHA NA INFRA

A 443 da .164 aceita conexão de qualquer origem (ufw `443 ALLOW Anywhere`, sem
lista de ranges da Cloudflare no nginx). Quem bater direto no IP da VPS,
contornando a Cloudflare, ainda escolhe o `CF-Connecting-IP`. O fecho é o mesmo
da TSK-286 no ConectaAP: aceitar na 443 só os ranges da Cloudflare.
"""
import ipaddress

HEADER_CLOUDFLARE = "HTTP_CF_CONNECTING_IP"


def _eh_da_malha(ip_bruto):
    """True quando a conexão veio de dentro da infra (rede privada ou loopback).

    É o que separa "chegou pela cadeia de proxies" de "alguém falando direto com
    o gunicorn". Só no primeiro caso faz sentido acreditar num header de IP.
    """
    try:
        endereco = ipaddress.ip_address((ip_bruto or "").strip())
    except ValueError:
        return False
    return endereco.is_private or endereco.is_loopback


def _ip_valido(valor):
    """O endereço do header, se for um IP; senão `None`.

    Header com lixo não pode virar chave de balde nem ir para uma coluna
    `GenericIPAddressField`.
    """
    candidato = (valor or "").split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(candidato))
    except ValueError:
        return None


class RealIPMiddleware:
    """Normaliza `REMOTE_ADDR` e `X-Forwarded-For` para o IP real do visitante.

    Precisa ser o PRIMEIRO do `MIDDLEWARE`: o que roda antes dele enxerga o IP
    do container.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        meta = request.META
        original = meta.get("REMOTE_ADDR", "")
        meta["REMOTE_ADDR_ORIGINAL"] = original
        if "HTTP_X_FORWARDED_FOR" in meta:
            meta["X_FORWARDED_FOR_ORIGINAL"] = meta["HTTP_X_FORWARDED_FOR"]

        if _eh_da_malha(original):
            ip_real = _ip_valido(meta.get(HEADER_CLOUDFLARE))
            if ip_real:
                meta["REMOTE_ADDR"] = ip_real

        # Daqui em diante o X-Forwarded-For tem um endereço só: o que este
        # middleware decidiu. Quem ainda lê a primeira posição lê o IP real, e
        # não o texto que o cliente mandou.
        if meta.get("REMOTE_ADDR"):
            meta["HTTP_X_FORWARDED_FOR"] = meta["REMOTE_ADDR"]
        else:
            meta.pop("HTTP_X_FORWARDED_FOR", None)

        return self.get_response(request)


def ip_do_cliente(request):
    """O IP de quem fez a requisição, já normalizado pelo `RealIPMiddleware`.

    É o que vai para o Conecta ID no login (bloqueio de força bruta por origem)
    e para o comprovante de venda. Nunca leia o `X-Forwarded-For` direto: a
    primeira posição dele é escrita pelo cliente.
    """
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None
