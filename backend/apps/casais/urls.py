from django.urls import path

from .views import elite

urlpatterns = [
    # Sem barra final, como as outras rotas do Lobby (ver apps/catalogo/urls.py).
    path("casais/elite", elite, name="casais-elite"),
]
