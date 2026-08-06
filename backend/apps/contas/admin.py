"""
Onde a diretoria promove alguém.

É aqui que se define quem autoriza negociação (gerente) e quem publica a tabela
(diretoria). O e-mail e o nome vêm do Conecta ID e são sincronizados a cada
login — corrigi-los aqui não adianta, o próximo login sobrescreve.
"""
from django.contrib import admin

from .models import VinculoIdentidade


@admin.register(VinculoIdentidade)
class VinculoIdentidadeAdmin(admin.ModelAdmin):
    list_display = ("email", "nome", "papel", "precisa_trocar_senha", "atualizado_em")
    list_filter = ("papel", "precisa_trocar_senha")
    list_editable = ("papel",)
    search_fields = ("usuario__email", "usuario__first_name", "usuario__last_name")
    ordering = ("usuario__email",)
    # O UUID e o usuário são o vínculo em si: mexer neles à mão é como duas
    # contas passam a apontar para a mesma identidade.
    readonly_fields = ("identidade_id", "usuario", "precisa_trocar_senha",
                       "criado_em", "atualizado_em")

    def has_add_permission(self, request):
        """Vínculo nasce no primeiro login, não no formulário."""
        return False

    @admin.display(description="e-mail", ordering="usuario__email")
    def email(self, obj):
        return obj.usuario.email

    @admin.display(description="nome")
    def nome(self, obj):
        return obj.usuario.get_full_name()
