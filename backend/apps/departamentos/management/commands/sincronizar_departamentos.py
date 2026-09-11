"""
Traz os departamentos do Omie para o banco do Lobby.

    python manage.py sincronizar_departamentos                # uma vez, e sai
    python manage.py sincronizar_departamentos --a-cada 3600  # para sempre

A segunda forma é o `command` do container `lobby-departamentos` no compose. É um
laço, e não um cron do host, para que o agendamento suba e desça com a stack,
fique versionado aqui e não dependa de alguém lembrar de um arquivo em
/etc/cron.d numa VPS reprovisionada.

O LAÇO NÃO PODE MORRER POR CAUSA DE UMA RODADA RUIM. Omie fora do ar, chave
revogada, túnel do banco caído: tudo isso é logado, e a próxima rodada tenta de
novo. Uma exceção que escapasse derrubaria o container, e o `restart` o subiria
chamando o Omie na mesma hora — em laço apertado, até o Omie bloquear a chave
por consumo redundante.
"""
import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.departamentos.omie import OmieIndisponivel, listar_departamentos
from apps.departamentos.servicos import ListagemVazia, sincronizar

logger = logging.getLogger("apps.departamentos")

# O Omie recusa a mesma chamada repetida em sequência ("consumo redundante").
INTERVALO_MINIMO = 60


class Command(BaseCommand):
    help = "Traz a lista de departamentos do Omie para o banco do Lobby."

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

        self.stdout.write(f"Sincronizando os departamentos do Omie a cada {a_cada} s.")
        while True:
            self.rodada()
            # O processo vive dias. Uma conexão aberta há uma hora pode ter
            # morrido junto com o túnel do banco — e toda rodada seguinte
            # falharia na mesma conexão quebrada. Fechar aqui faz a próxima
            # abrir uma nova; o custo é uma conexão por hora.
            connections.close_all()
            time.sleep(a_cada)

    def rodada(self) -> bool:
        try:
            resultado = sincronizar(listar_departamentos())
        except (OmieIndisponivel, ListagemVazia) as erro:
            self.stderr.write(f"Departamentos NÃO sincronizados: {erro}")
            return False
        except Exception:
            logger.exception("Falha inesperada ao sincronizar os departamentos do Omie.")
            return False
        self.stdout.write(f"Departamentos sincronizados: {resultado}")
        return True
