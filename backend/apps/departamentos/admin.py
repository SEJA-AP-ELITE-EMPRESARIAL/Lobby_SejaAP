from django.contrib import admin

from .models import ConfiguracaoDepartamento, Departamento


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


@admin.register(ConfiguracaoDepartamento)
class ConfiguracaoDepartamentoAdmin(admin.ModelAdmin):
    """Somente leitura: quem configura é a diretoria, na aba Departamentos do
    `/admin`. Gravar por aqui pularia o autor e a validação de departamento
    ativo — e editar uma linha antiga reescreveria o registro de quem decidiu.
    """

    list_display = ("criado_em", "modo", "departamento", "autor_email")
    list_filter = ("modo",)
    readonly_fields = tuple(f.name for f in ConfiguracaoDepartamento._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
