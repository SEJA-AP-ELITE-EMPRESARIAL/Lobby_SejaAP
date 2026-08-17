"""
Administração do catálogo.

Divisão de trabalho, e ela mudou em 17/08/2026:

- A tela `/admin` (o `admin.html`, React) é onde a diretoria trabalha. Ela edita
  PREÇO, as DATAS de cobrança e agora também cria e edita PRODUTO — tudo pelo
  serviço, que grava publicação, autor e vigência.
- Este `/django-admin/` virou o caminho de CONSERTO: mexer em categoria, corrigir
  o que a tela não alcança, olhar o histórico cru. Continua útil, mas não é mais
  o único jeito de criar produto — era essa a distorção, porque o caminho de
  menor esforço era justamente o que não deixava rastro.

O aviso que segue valendo: alteração feita por aqui **não** entra no histórico de
publicações nem abre vigência. Para o valor de um produto, use o `/admin`.
"""
from django.contrib import admin

from .models import (
    Categoria,
    PoliticaCobranca,
    Produto,
    PublicacaoCatalogo,
    Vigencia,
)


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


@admin.register(PoliticaCobranca)
class PoliticaCobrancaAdmin(admin.ModelAdmin):
    """A regra de data do cronograma. O caminho normal é o `/admin`."""

    list_display = ("__str__", "geral", "produto", "dia_vencimento", "primeiro_vencimento", "entrada_prazo_dias")
    list_filter = ("geral", "primeiro_vencimento")

    def has_delete_permission(self, request, obj=None):
        # A política geral não pode sumir: sem ela o front não monta cronograma.
        # Exceção de produto se remove no /admin, que fecha a vigência junto.
        return False


@admin.register(Vigencia)
class VigenciaAdmin(admin.ModelAdmin):
    """Somente leitura: é registro do que aconteceu, não formulário.

    Editar uma vigência à mão é reescrever o passado — e a tabela existe
    exatamente para que o passado não seja reescrito.
    """

    list_display = ("vigente_de", "vigente_ate", "rotulo", "campo", "valor", "autor_email")
    list_filter = ("campo", "chave")
    search_fields = ("chave", "rotulo", "valor", "autor_email")
    ordering = ("-vigente_de",)
    readonly_fields = tuple(f.name for f in Vigencia._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
