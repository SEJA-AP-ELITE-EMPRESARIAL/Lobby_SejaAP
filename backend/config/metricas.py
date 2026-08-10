"""
Métricas para o Prometheus, no padrão do Conecta ID (`apps/identidade/metricas.py`).

O que se quer enxergar aqui não é uso, é **anomalia**. O Lobby tem duas falhas
possíveis que ninguém percebe olhando a tela:

1. O catálogo esvaziar. O front cai no `CATS` embutido (`index.html:2216-2228`)
   e continua vendendo — com a tabela de preços de quando o HTML foi escrito.
   Degradar em silêncio é pior do que quebrar.
2. O n8n parar de validar o comprovante de venda. Aí a assinatura continua sendo
   emitida e ninguém confere nada: a brecha do valor negociado reabre sem que
   uma única linha de log diga isso.

Nenhuma das duas dispara erro. Por isso é métrica, e não alerta de exceção.

POR QUE TUDO SAI DE `count()` NO BANCO, E NÃO DE CONTADOR EM MEMÓRIA

Contador de processo zera a cada deploy e é por worker — o gunicorn responderia
um número diferente a cada coleta. O Conecta ID resolve isso contando linhas de
uma tabela de auditoria; aqui não existe tabela de evento (é o escopo da
TSK-128), então conta-se o que já é persistido: comprovantes, publicações do
catálogo e vínculos.

A CONSEQUÊNCIA DISSO, QUE É UMA LIMITAÇÃO REAL

Os MOTIVOS de recusa (`valores_adulterados`, `assinatura_invalida`,
`comprovante_ja_usado`, `comprovante_desconhecido`) não aparecem aqui. Eles só
existem no log — `apps/vendas/views.py:114`. Medi-los exige persistir evento, o
que é mudança de esquema e pertence à auditoria. O que este módulo oferece no
lugar é indireto e, para o risco de hoje, suficiente: dá para ver se o n8n está
validando comparando emitidos com consumidos.

O endpoint é fechado por token. Sem `LOBBY_METRICS_TOKEN` configurado ele **não
existe** (404): um /metrics aberto entrega o volume de vendas da empresa e a
tabela de quem pode autorizar desconto.
"""
import hmac

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.catalogo.models import Categoria, Produto, PublicacaoCatalogo
from apps.contas.models import Papel, VinculoIdentidade
from apps.vendas.models import ComprovanteVenda


@require_GET
def metricas(request):
    token = getattr(settings, "LOBBY_METRICS_TOKEN", "")
    if not token:
        return JsonResponse(
            {"erro": "nao_encontrado", "detalhe": "Métricas desabilitadas."}, status=404
        )

    enviado = request.META.get("HTTP_AUTHORIZATION", "")
    prefixo = "Bearer "
    # `compare_digest` em vez de `==`: a comparação byte a byte do Python sai no
    # primeiro caractere diferente, e isso é medível.
    if not enviado.startswith(prefixo) or not hmac.compare_digest(
        enviado[len(prefixo):], token
    ):
        return JsonResponse(
            {"erro": "app_nao_autorizado", "detalhe": "Token inválido."}, status=401
        )

    return HttpResponse(
        "\n".join(_coletar()) + "\n", content_type="text/plain; version=0.0.4"
    )


def _idade_em_segundos(momento) -> int | None:
    if momento is None:
        return None
    return int((timezone.now() - momento).total_seconds())


def _coletar():
    agora = timezone.now()
    linhas = [
        "# HELP lobby_up O serviço respondeu à coleta.",
        "# TYPE lobby_up gauge",
        "lobby_up 1",
    ]

    # ---- comprovantes de venda ------------------------------------------
    #
    # `counter` e não `gauge` porque o que interessa é a DERIVADA:
    # `increase(...[6h])` responde "quantas vendas nas últimas 6 horas", que é a
    # pergunta dos alertas. Expurgo de linhas antigas zera o valor, e o
    # Prometheus trata queda de counter como reset — perde-se um ponto, não a
    # série.
    #
    # `expirado_sem_uso` é cumulativo de verdade: comprovante que expirou sem
    # ser consumido nunca mais muda de estado.
    comprovantes = ComprovanteVenda.objects.aggregate(
        emitido=Count("id"),
        consumido=Count("id", filter=Q(consumido_em__isnull=False)),
        negociado=Count("id", filter=Q(negociado=True)),
        expirado_sem_uso=Count(
            "id", filter=Q(consumido_em__isnull=True, expira_em__lt=agora)
        ),
    )
    linhas += [
        "# HELP lobby_comprovantes_total Comprovantes de venda acumulados, por estado.",
        "# TYPE lobby_comprovantes_total counter",
    ]
    for estado in ("emitido", "consumido", "negociado", "expirado_sem_uso"):
        linhas.append(
            f'lobby_comprovantes_total{{estado="{estado}"}} {comprovantes[estado]}'
        )

    # Frescor das duas pontas do ciclo. A distância entre elas é o sinal:
    # emissão recente com validação parada significa que o n8n saiu do ar ou
    # deixou de chamar /api/venda/validar.
    #
    # NÃO existe alerta sobre "faz muito tempo que ninguém emite" de propósito:
    # venda é esporádica, e um alerta desses tocaria todo fim de semana. A série
    # serve para o painel, onde um humano olha com contexto.
    ultima_emissao = _idade_em_segundos(
        ComprovanteVenda.objects.order_by("-emitido_em")
        .values_list("emitido_em", flat=True)
        .first()
    )
    ultima_validacao = _idade_em_segundos(
        ComprovanteVenda.objects.filter(consumido_em__isnull=False)
        .order_by("-consumido_em")
        .values_list("consumido_em", flat=True)
        .first()
    )
    if ultima_emissao is not None:
        linhas += [
            "# HELP lobby_segundos_desde_ultimo_comprovante Idade da última emissão.",
            "# TYPE lobby_segundos_desde_ultimo_comprovante gauge",
            f"lobby_segundos_desde_ultimo_comprovante {ultima_emissao}",
        ]
    if ultima_validacao is not None:
        linhas += [
            "# HELP lobby_segundos_desde_ultima_validacao Idade da última validação feita pelo n8n.",
            "# TYPE lobby_segundos_desde_ultima_validacao gauge",
            f"lobby_segundos_desde_ultima_validacao {ultima_validacao}",
        ]

    # ---- integração com o n8n -------------------------------------------
    #
    # Sem o segredo configurado, /api/venda/validar responde 503 e o n8n cai no
    # ramo de conferência manual. É recuperável, mas silencioso do lado do
    # Lobby: nada falha aqui. Um deploy que perca a variável de ambiente
    # desligaria a validação sem ninguém notar.
    linhas += [
        "# HELP lobby_n8n_configurado O segredo compartilhado com o n8n está presente.",
        "# TYPE lobby_n8n_configurado gauge",
        f"lobby_n8n_configurado {1 if getattr(settings, 'LOBBY_N8N_TOKEN', '') else 0}",
    ]

    # ---- catálogo --------------------------------------------------------
    #
    # Zero produto é o estado que o front esconde: ele cai no fallback embutido
    # e segue vendendo com preço velho.
    linhas += [
        "# HELP lobby_catalogo Tamanho do catálogo servido hoje.",
        "# TYPE lobby_catalogo gauge",
        f'lobby_catalogo{{item="categorias"}} {Categoria.objects.count()}',
        f'lobby_catalogo{{item="produtos"}} {Produto.objects.count()}',
        f'lobby_catalogo{{item="categorias_travadas"}} '
        f"{Categoria.objects.filter(travada=True).count()}",
    ]

    linhas += [
        "# HELP lobby_publicacoes_total Publicações da tabela de preços acumuladas.",
        "# TYPE lobby_publicacoes_total counter",
        f"lobby_publicacoes_total {PublicacaoCatalogo.objects.count()}",
    ]
    ultima_publicacao = _idade_em_segundos(
        PublicacaoCatalogo.objects.order_by("-publicado_em")
        .values_list("publicado_em", flat=True)
        .first()
    )
    if ultima_publicacao is not None:
        linhas += [
            "# HELP lobby_segundos_desde_ultima_publicacao Idade da última publicação.",
            "# TYPE lobby_segundos_desde_ultima_publicacao gauge",
            f"lobby_segundos_desde_ultima_publicacao {ultima_publicacao}",
        ]

    # ---- gente -----------------------------------------------------------
    #
    # Zero pessoa na diretoria é um estado travado, não uma curiosidade: sem
    # ela ninguém publica a tabela e ninguém alcança o /django-admin/. Acontece
    # de verdade — foi o estado do corte para a .164, quando o app subiu antes
    # de alguém ser promovido.
    papeis = {
        linha["papel"]: linha["n"]
        for linha in VinculoIdentidade.objects.values("papel").annotate(n=Count("id"))
    }
    linhas += [
        "# HELP lobby_pessoas Vínculos de identidade por papel.",
        "# TYPE lobby_pessoas gauge",
        f'lobby_pessoas{{papel="diretoria"}} {papeis.get(Papel.DIRETORIA.value, 0)}',
        f'lobby_pessoas{{papel="gerente"}} {papeis.get(Papel.GERENTE.value, 0)}',
        f'lobby_pessoas{{papel="sem_papel"}} {papeis.get("", 0)}',
    ]

    Usuario = get_user_model()
    linhas += [
        "# HELP lobby_contas Contas locais por estado.",
        "# TYPE lobby_contas gauge",
        f'lobby_contas{{estado="ativa"}} {Usuario.objects.filter(is_active=True).count()}',
        f'lobby_contas{{estado="inativa"}} {Usuario.objects.filter(is_active=False).count()}',
    ]
    return linhas
