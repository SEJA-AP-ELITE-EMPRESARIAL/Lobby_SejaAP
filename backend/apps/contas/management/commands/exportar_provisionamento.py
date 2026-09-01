"""
Exporta o papel de cada pessoa no Lobby, para o Conecta ID guardar.

    python manage.py exportar_provisionamento > lobby.json

Só leitura: não toca em conta nenhuma, aqui nem lá. O arquivo é consumido pelo
`importar_provisionamento` do Conecta ID, que grava a configuração dos acessos.

Este app **não pode** escrever a própria configuração pela API: `PATCH /acessos`
exige `pode_gerir_identidades`, que ele não tem — e não deveria ter, porque a
flag também libera editar qualquer identidade e enxergar a base inteira.

Quem não tem papel sai com configuração vazia, e o import pula. É o certo: sem
papel não é um papel, é a ausência de um — a pessoa entra e não autoriza nada,
e o admin do Conecta ID mostrando "—" para ela diz exatamente isso.
"""
import json

from django.core.management.base import BaseCommand

from apps.contas.models import VinculoIdentidade


class Command(BaseCommand):
    help = "Exporta o papel de cada conta vinculada, em JSON, para o Conecta ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--incluir-inativos",
            action="store_true",
            help="Também exporta contas desativadas aqui. Fora por padrão.",
        )

    def handle(self, *args, **opcoes):
        vinculos = VinculoIdentidade.objects.select_related("usuario").order_by(
            "usuario__email"
        )
        if not opcoes["incluir_inativos"]:
            vinculos = vinculos.filter(usuario__is_active=True)

        itens = []
        for vinculo in vinculos:
            configuracao = configuracao_de(vinculo)
            if not configuracao:
                continue
            itens.append(
                {
                    "identidade_id": str(vinculo.identidade_id),
                    "email": vinculo.usuario.email,
                    "configuracao": configuracao,
                }
            )

        self.stdout.write(
            json.dumps({"app": "lobby", "itens": itens}, ensure_ascii=False, indent=2)
        )
        self.stderr.write(f"{len(itens)} conta(s) exportada(s).")


def configuracao_de(vinculo):
    """O papel desta pessoa no Lobby, ou `{}` quando ela não tem nenhum."""
    return {"papel": vinculo.papel} if vinculo.papel else {}
