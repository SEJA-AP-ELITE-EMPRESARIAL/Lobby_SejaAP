from django.contrib import admin

from .models import Departamento


@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    """Somente leitura: a fonte é o Omie.

    Editar aqui não teria efeito que dure — a próxima sincronização, em no
    máximo uma hora, sobrescreveria. Departamento se cria, renomeia e inativa
    no Omie; para trazer a mudança na hora, rode `sincronizar_departamentos`.
    """

    list_display = ("descricao", "codigo", "estrutura", "ativo", "visto_em", "alterado_em")
    list_filter = ("ativo",)
    search_fields = ("descricao", "codigo")
    ordering = ("estrutura", "descricao")
    readonly_fields = tuple(f.name for f in Departamento._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
