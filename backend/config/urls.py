"""
Rotas do projeto.

O admin do Django fica em `/django-admin/`, NÃO em `/admin/`. O motivo é
concreto: `/admin` já é a tela de valores da diretoria (`admin.html`), servida
como arquivo estático pelo nginx. É a mesma solução do Formulários Financeiro,
pelo mesmo motivo.
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/", include("apps.catalogo.urls")),
    path("api/", include("apps.contas.urls")),
    path("api/", include("apps.vendas.urls")),
]

admin.site.site_header = "Lobby Seja AP"
admin.site.site_title = "Lobby Seja AP"
admin.site.index_title = "Administração"
