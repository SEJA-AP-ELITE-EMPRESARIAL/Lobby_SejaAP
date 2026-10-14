from django.contrib import admin

from .models import CategoriaFinanceira, ConfiguracaoCategoriaFinanceira


@admin.register(CategoriaFinanceira)
class CategoriaFinanceiraAdmin(admin.ModelAdmin):
    """Somente leitura: a fonte manda, e a próxima sincronização sobrescreveria
    qualquer edição. Para trazer uma mudança na hora, rode
    `sincronizar_categorias_financeiras`."""

    list_display = ("descricao", "codigo", "ativo", "visto_em", "alterado_em")
    list_filter = ("ativo",)
    search_fields = ("descricao", "codigo")
    readonly_fields = tuple(f.name for f in CategoriaFinanceira._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConfiguracaoCategoriaFinanceira)
class ConfiguracaoCategoriaFinanceiraAdmin(admin.ModelAdmin):
    """Somente leitura: quem configura é a diretoria, na aba Categorias do
    `/admin`, que guarda o autor e recusa categoria inativa."""

    list_display = ("criado_em", "modo", "categoria", "autor_email")
    list_filter = ("modo",)
    readonly_fields = tuple(f.name for f in ConfiguracaoCategoriaFinanceira._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
