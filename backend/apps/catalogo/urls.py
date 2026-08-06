"""
Rotas do catálogo.

O caminho é `/api/catalogo` — sem barra final, sem prefixo de app, GET e PUT no
mesmo lugar. Não é escolha de estilo: `index.html:2218` e `admin.html:173,222`
chamam essa URL literal, e são servidos como arquivo estático, então não dá para
mudar os dois lados no mesmo deploy.

Atenção ao `APPEND_SLASH` do Django: com barra final registrada, um PUT em
`/api/catalogo` levaria 301 para `/api/catalogo/` e o corpo se perderia no
caminho. Por isso a rota é declarada exatamente como o front a chama.

Mesma origem, sempre. O front usa URL relativa; o nginx serve o HTML e faz
proxy de `/api/` para cá. É por isso que este projeto não instala
`django-cors-headers`.
"""
from django.urls import path

from .views import CatalogoView

urlpatterns = [
    path("catalogo", CatalogoView.as_view(), name="catalogo"),
]
