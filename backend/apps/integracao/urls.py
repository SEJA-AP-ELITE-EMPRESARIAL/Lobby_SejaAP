"""
Rotas da integração com o Conecta (ADR-002 §13.4).

Os NOMES importam mais que os caminhos: é por `url_name` e método que a lista
fechada da asserção é conferida (`apps.contas.autenticacao_conecta.ROTAS_LIBERADAS`).
Renomear uma rota aqui sem mexer lá a tira do ar para o Conecta — e é de
propósito: rota nova não entra na lista sem alguém decidir.

Sem barra final, como o resto do Lobby: o `APPEND_SLASH` transformaria um POST ou
um PATCH em 301 e o corpo se perderia no caminho.
"""
from django.urls import path

from .views import CategoriaView, HistoricoView, PublicarCatalogoView, QuemSouView, SaudeView

urlpatterns = [
    path("integracao/quem-sou", QuemSouView.as_view(), name="integracao-quem-sou"),
    path("integracao/saude", SaudeView.as_view(), name="integracao-saude"),
    path(
        "integracao/catalogo/publicar",
        PublicarCatalogoView.as_view(),
        name="integracao-catalogo-publicar",
    ),
    path(
        "integracao/categorias/<slug:slug>",
        CategoriaView.as_view(),
        name="integracao-categoria",
    ),
    path("integracao/historico", HistoricoView.as_view(), name="integracao-historico"),
]
