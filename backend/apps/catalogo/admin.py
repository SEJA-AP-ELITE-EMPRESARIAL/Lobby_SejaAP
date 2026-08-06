"""
Administração do catálogo.

Divisão de trabalho, e ela é intencional:

- A tela `/admin` (o `admin.html`, React) é onde a diretoria mexe em PREÇO. Ela
  edita um número por produto e mais nada, e toda publicação passa pelo serviço,
  que grava histórico com autor.
- Este `/django-admin/` é onde se faz MUDANÇA ESTRUTURAL: criar produto, mudar
  nome, reordenar, travar categoria. É raro, é consciente, e não é o caminho do
  dia a dia.

Por isso os campos de valor aparecem aqui, mas com o aviso: alteração feita
por aqui NÃO entra no histórico de publicações.
"""
from django.contrib import admin

from .models import Categoria, Produto, PublicacaoCatalogo


class ProdutoInline(admin.TabularInline):
    model = Produto
    extra = 0
    fields = (
        "ordem",
        "slug",
        "nome",
        "sigla",
        "recorrente",
        "mensalidade",
        "vigencia_meses",
        "valor",
        "icone",
    )
    ordering = ("ordem", "id")


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("ordem", "nome", "slug", "cor", "travada", "fluxo", "qtd_produtos")
    list_display_links = ("nome",)
    list_editable = ("ordem",)
    ordering = ("ordem", "id")
    search_fields = ("nome", "slug")
    inlines = [ProdutoInline]
    fieldsets = (
        (None, {"fields": ("slug", "nome", "descricao", "ordem")}),
        ("Aparência no lobby", {"fields": ("icone", "cor", "travada")}),
        (
            "Fluxo próprio",
            {
                "fields": ("fluxo", "sigla"),
                "description": (
                    "Preencha <code>fluxo</code> só em categoria sem tabela de preços "
                    "(hoje, a APN). Ela passa a ser somente leitura para a tela da "
                    "diretoria, e a <code>sigla</code> alimenta o protocolo da venda "
                    "no lugar da sigla do produto."
                ),
            },
        ),
    )

    @admin.display(description="produtos")
    def qtd_produtos(self, obj):
        return obj.produtos.count()


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "categoria",
        "sigla",
        "recorrente",
        "mensalidade",
        "vigencia_meses",
        "valor",
        "preco",
    )
    list_filter = ("categoria", "recorrente")
    search_fields = ("nome", "slug", "sigla")
    ordering = ("categoria", "ordem", "id")

    @admin.display(description="total do contrato")
    def preco(self, obj):
        return obj.preco


@admin.register(PublicacaoCatalogo)
class PublicacaoCatalogoAdmin(admin.ModelAdmin):
    """Somente leitura: é registro do que aconteceu, não formulário."""

    list_display = ("publicado_em", "autor_email", "resumo_curto")
    ordering = ("-publicado_em",)
    search_fields = ("autor_email", "resumo")
    readonly_fields = ("publicado_em", "autor", "autor_email", "catalogo", "resumo")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="alterações")
    def resumo_curto(self, obj):
        if not obj.resumo:
            return "— (sem mudança de valor)"
        linhas = obj.resumo.splitlines()
        primeira = linhas[0]
        return primeira if len(linhas) == 1 else f"{primeira} (+{len(linhas) - 1})"
