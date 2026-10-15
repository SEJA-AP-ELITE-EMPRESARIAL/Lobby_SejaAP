from django.contrib import admin

from .models import ConfiguracaoValores


@admin.register(ConfiguracaoValores)
class ConfiguracaoValoresAdmin(admin.ModelAdmin):
    """Somente leitura: quem configura é a diretoria, na aba DH - Avaliação do
    `/admin`. Gravar por aqui pularia o autor — e editar uma linha antiga
    reescreveria o registro de quem decidiu.
    """

    list_display = (
        "criado_em",
        "relatorio_valor",
        "relatorio_fixo",
        "devolutiva_valor",
        "devolutiva_fixo",
        "autor_email",
    )
    readonly_fields = tuple(f.name for f in ConfiguracaoValores._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
