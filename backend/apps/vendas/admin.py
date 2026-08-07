"""
Comprovantes emitidos — somente leitura.

É registro do que aconteceu, e a tela mais útil quando alguém pergunta "quem
autorizou este desconto?". Filtrar por `negociado` mostra exatamente as vendas
que saíram da tabela.
"""
from django.contrib import admin

from .models import ComprovanteVenda


@admin.register(ComprovanteVenda)
class ComprovanteVendaAdmin(admin.ModelAdmin):
    list_display = ("protocolo", "fluxo", "negociado", "autorizador_email",
                    "total", "emitido_em", "situacao")
    list_filter = ("fluxo", "negociado", "emitido_em")
    search_fields = ("protocolo", "autorizador_email")
    ordering = ("-emitido_em",)
    readonly_fields = tuple(f.name for f in ComprovanteVenda._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="valor total")
    def total(self, obj):
        return (obj.valores or {}).get("valor_total", "—")

    @admin.display(description="situação")
    def situacao(self, obj):
        if obj.consumido:
            return "usado"
        return "expirado" if obj.expirado else "aguardando"
