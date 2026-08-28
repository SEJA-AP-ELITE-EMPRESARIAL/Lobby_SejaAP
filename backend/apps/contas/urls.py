"""
Rotas de sessão e de senha.

`/api/sessao` é o caminho que o front já chama (`index.html:197`,
`admin.html:114`) — mantido literal para que a troca de senha compartilhada por
login por pessoa mexa só no corpo da requisição, não na URL.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("sessao", views.abrir_sessao, name="abrir-sessao"),
    path("sessao/atual", views.sessao_atual, name="sessao-atual"),
    # As duas metades do ciclo da senha, ambas anônimas: quem chega aqui é
    # justamente quem ainda não consegue entrar. `esqueci` pede o link, que o
    # Conecta ID manda por e-mail; `definir` consome o token daquele link.
    path("senha/esqueci", views.esqueci_senha, name="esqueci-senha"),
    path("senha/definir", views.definir_senha, name="definir-senha"),
]
