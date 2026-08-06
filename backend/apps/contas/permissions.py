"""
As duas permissões do Lobby.

A separação é de alcance, não de hierarquia decorativa: autorizar negociação
afeta UMA venda; publicar a tabela afeta TODAS as próximas.
"""
from rest_framework.permissions import BasePermission


class PodeAutorizarNegociacao(BasePermission):
    """Gerente ou diretoria — destrava valor e cronograma numa venda."""

    message = (
        "Sua conta não tem permissão para autorizar negociação. "
        "Peça à diretoria para liberar."
    )

    def has_permission(self, request, view):
        vinculo = getattr(request.user, "vinculo_identidade", None)
        return bool(vinculo and vinculo.pode_autorizar_negociacao)


class PodePublicarTabela(BasePermission):
    """Só diretoria — altera a tabela de preços que todo consultor enxerga."""

    message = "Somente a diretoria pode alterar a tabela de preços."

    def has_permission(self, request, view):
        vinculo = getattr(request.user, "vinculo_identidade", None)
        return bool(vinculo and vinculo.pode_publicar_tabela)
