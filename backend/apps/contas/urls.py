"""
Rotas de sessão.

`/api/sessao` é o caminho que o front já chama (`index.html:197`,
`admin.html:114`) — mantido literal para que a troca de senha compartilhada por
login por pessoa mexa só no corpo da requisição, não na URL.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("sessao", views.abrir_sessao, name="abrir-sessao"),
    path("sessao/atual", views.sessao_atual, name="sessao-atual"),
]
