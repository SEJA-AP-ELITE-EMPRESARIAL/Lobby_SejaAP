"""
Rotas do projeto.

O admin do Django fica em `/django-admin/`, NÃO em `/admin/`. O motivo é
concreto: `/admin` já é a tela de valores da diretoria (`admin.html`), servida
como arquivo estático pelo nginx. É a mesma solução do Formulários Financeiro,
pelo mesmo motivo.

`/metrics` é do Prometheus, fechado por token. Sem token configurado, some (404).
Fica fora de `/api/` de propósito: o nginx da borda não o expõe, e quem coleta
alcança o container pelo loopback do host.

`/api/diagnostico/erro-de-teste` levanta um erro de propósito, só para
superusuário, para conferir o GlitchTip depois do deploy (TSK-1142).
"""
from django.contrib import admin
from django.urls import include, path

from .diagnostico import ErroDeTesteView
from .metricas import metricas

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("metrics", metricas, name="metricas"),
    path("api/", include("apps.catalogo.urls")),
    path("api/", include("apps.contas.urls")),
    path("api/", include("apps.vendas.urls")),
    path("api/", include("apps.departamentos.urls")),
    path("api/", include("apps.categorias_financeiras.urls")),
    path("api/", include("apps.dh_avaliacao.urls")),
    path("api/", include("apps.integracao.urls")),
    path(
        "api/diagnostico/erro-de-teste",
        ErroDeTesteView.as_view(),
        name="diagnostico-erro-de-teste",
    ),
]

admin.site.site_header = "Lobby Seja AP"
admin.site.site_title = "Lobby Seja AP"
admin.site.index_title = "Administração"
