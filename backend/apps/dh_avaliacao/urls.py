from django.urls import path

from .views import ConfiguracaoView, ValoresView

urlpatterns = [
    # Sem barra final, como as outras rotas do Lobby (ver apps/catalogo/urls.py):
    # o `APPEND_SLASH` transformaria o PUT da configuração em 301 e o corpo se
    # perderia no caminho.
    path("dh-avaliacao/valores", ValoresView.as_view(), name="dh-avaliacao-valores"),
    path(
        "dh-avaliacao/valores/configuracao",
        ConfiguracaoView.as_view(),
        name="dh-avaliacao-valores-configuracao",
    ),
]
