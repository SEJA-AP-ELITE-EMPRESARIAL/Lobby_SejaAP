from django.urls import path

from .views import ConfiguracaoView, DepartamentosView

urlpatterns = [
    # Sem barra final, como as outras rotas do Lobby (ver apps/catalogo/urls.py):
    # o `APPEND_SLASH` transformaria o PUT da configuração em 301 e o corpo se
    # perderia no caminho.
    path("departamentos", DepartamentosView.as_view(), name="departamentos"),
    path(
        "departamentos/configuracao",
        ConfiguracaoView.as_view(),
        name="departamentos-configuracao",
    ),
]
