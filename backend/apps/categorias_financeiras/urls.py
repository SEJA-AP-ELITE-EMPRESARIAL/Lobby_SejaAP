from django.urls import path

from .views import CategoriasFinanceirasView, ConfiguracaoView

urlpatterns = [
    # Sem barra final, como as outras rotas do Lobby: o `APPEND_SLASH`
    # transformaria o PUT da configuração em 301 e o corpo se perderia.
    path("categorias-financeiras", CategoriasFinanceirasView.as_view(), name="categorias-financeiras"),
    path(
        "categorias-financeiras/configuracao",
        ConfiguracaoView.as_view(),
        name="categorias-financeiras-configuracao",
    ),
]
