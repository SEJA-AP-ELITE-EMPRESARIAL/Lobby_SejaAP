"""Rotas de venda."""
from django.urls import path

from . import views

urlpatterns = [
    path("venda/comprovante", views.emitir_comprovante, name="venda-comprovante"),
    path("venda/validar", views.validar_comprovante, name="venda-validar"),
]
