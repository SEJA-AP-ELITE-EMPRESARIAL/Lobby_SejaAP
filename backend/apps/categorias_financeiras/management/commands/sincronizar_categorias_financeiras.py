"""
Traz as categorias financeiras da fonte para o banco do Lobby.

    python manage.py sincronizar_categorias_financeiras                # uma vez, e sai
    python manage.py sincronizar_categorias_financeiras --a-cada 3600  # para sempre

A segunda forma é o `command` do container `lobby-categorias` no compose. Mesmo
laço do `sincronizar_departamentos`, pelos mesmos motivos: nenhuma rodada ruim
derruba o processo, porque o `restart` o subiria chamando a fonte de novo na
mesma hora, em laço apertado.

Enquanto a API não for ligada (ver `fonte.py`), toda rodada loga
"NÃO sincronizadas" e o banco fica vazio.
"""
import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.categorias_financeiras.fonte import FonteIndisponivel, listar_categorias
from apps.categorias_financeiras.servicos import ListagemVazia, sincronizar

logger = logging.getLogger("apps.categorias_financeiras")

# Rede contra um intervalo que martele a fonte.
INTERVALO_MINIMO = 60


class Command(BaseCommand):
    help = "Traz a lista de categorias financeiras da fonte para o banco do Lobby."

    def add_arguments(self, parser):
        parser.add_argument(
            "--a-cada",
            dest="a_cada",
            type=int,
            default=0,
            metavar="SEGUNDOS",
            help="Repete para sempre com este intervalo (o compose usa 3600). "
            "Sem ele, roda uma vez e sai.",
        )

    def handle(self, *args, a_cada=0, **opcoes):
        if not a_cada:
            if not self.rodada():
                raise CommandError("A sincronização falhou — ver a mensagem acima.")
            return

        if a_cada < INTERVALO_MINIMO:
            raise CommandError(f"--a-cada precisa ser de pelo menos {INTERVALO_MINIMO} segundos.")

        self.stdout.write(f"Sincronizando as categorias financeiras a cada {a_cada} s.")
        while True:
            self.rodada()
            # Conexão nova a cada rodada: a de uma hora atrás pode ter morrido
            # com o túnel do banco.
            connections.close_all()
            time.sleep(a_cada)

    def rodada(self) -> bool:
        try:
            resultado = sincronizar(listar_categorias())
        except (FonteIndisponivel, ListagemVazia) as erro:
            self.stderr.write(f"Categorias financeiras NÃO sincronizadas: {erro}")
            return False
        except Exception:
            logger.exception("Falha inesperada ao sincronizar as categorias financeiras.")
            return False
        self.stdout.write(f"Categorias financeiras sincronizadas: {resultado}")
        return True
