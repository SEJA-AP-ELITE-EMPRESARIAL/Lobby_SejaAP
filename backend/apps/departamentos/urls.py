from django.urls import path

from .views import DepartamentosView

urlpatterns = [
    # Sem barra final, como as outras rotas do Lobby (ver apps/catalogo/urls.py).
    path("departamentos", DepartamentosView.as_view(), name="departamentos"),
]
