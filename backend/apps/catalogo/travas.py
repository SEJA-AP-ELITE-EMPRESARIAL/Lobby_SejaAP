"""
Liberar ou tirar uma categoria do Lobby — `Categoria.travada` (TSK-1059).

Até aqui só mudava pelo /django-admin/, sem autor e sem rastro: alguém liberava
Treinamentos e ninguém sabia depois quem, nem quando. Agora a mudança passa por
aqui, com a linha do tempo (`Vigencia`, campo `travada`, chave
`categoria:<slug>`) e o autor de cada lado.

Travada, a categoria aparece no lobby como "Em implementação" e não abre — vale
para todo consultor no instante seguinte, e por isso é da diretoria.
"""
from django.db import transaction
from django.utils import timezone

from .models import Categoria, Vigencia
from .vigencias import NAO, SIM, registrar


def rotulo_da_trava(valor: str) -> str:
    return "fora do Lobby (em implementação)" if valor == SIM else "no Lobby"


@transaction.atomic
def altera_travada(categoria: Categoria, travada: bool, *, autor) -> str | None:
    """Grava o novo estado. Devolve a frase da mudança, ou None se nada mudou."""
    categoria = Categoria.objects.select_for_update().get(pk=categoria.pk)
    if categoria.travada == travada:
        return None
    categoria.travada = travada
    categoria.save(update_fields=["travada", "atualizado_em"])
    registrar(
        Vigencia.PREFIXO_CATEGORIA + categoria.slug,
        categoria.nome,
        Vigencia.Campo.TRAVADA,
        SIM if travada else NAO,
        autor=autor,
        quando=timezone.now(),
    )
    return f"{categoria.nome}: {'tirada do Lobby (em implementação)' if travada else 'liberada no Lobby'}"
