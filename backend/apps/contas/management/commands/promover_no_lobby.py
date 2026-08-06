"""
Define o papel de alguém no Lobby.

Resolve o problema do ovo e da galinha: como `AUTHENTICATION_BACKENDS` só tem o
`BackendIdentidade`, não existe login local — nem para superusuário. E como
ninguém nasce com papel, a primeira pessoa da diretoria não teria como alcançar
o /django-admin/ para se promover. Este comando é a porta de entrada, e roda no
container:

    docker exec lobby-backend python manage.py promover_no_lobby \\
        fulano@sejaap.com.br --papel diretoria

Pré-requisito: a pessoa já precisa ter acesso ao app `lobby` no Conecta ID
(`POST /api/v1/acessos` ou o admin de lá). Sem isso o comando não a encontra —
e isso é correto, porque quem decide quem entra na empresa é o Conecta ID, não
este app.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from identidade_client import (
    ClienteIdentidade,
    ErroIdentidade,
    IdentidadeIndisponivel,
)

from apps.contas.models import Papel, VinculoIdentidade


class Command(BaseCommand):
    help = "Define o papel de uma pessoa no Lobby (gerente ou diretoria)."

    def add_arguments(self, parser):
        parser.add_argument("email", help="E-mail da pessoa no Conecta ID.")
        parser.add_argument(
            "--papel",
            choices=[Papel.GERENTE.value, Papel.DIRETORIA.value, ""],
            default=Papel.DIRETORIA.value,
            help="'gerente', 'diretoria' ou vazio para remover o papel.",
        )

    def handle(self, *args, **opcoes):
        email = opcoes["email"].strip().lower()
        papel = opcoes["papel"]

        try:
            dados = ClienteIdentidade().buscar_por_email(email)
        except IdentidadeIndisponivel as erro:
            raise CommandError(
                f"O Conecta ID não respondeu ({erro}). O container está na rede "
                "identidade-net e a IDENTIDADE_APP_KEY está configurada?"
            )
        except ErroIdentidade as erro:
            raise CommandError(f"Erro ao consultar o Conecta ID: {erro}")

        if not dados:
            raise CommandError(
                f"Não achei '{email}' entre as identidades com acesso ao app "
                "'lobby'. Conceda o acesso no Conecta ID primeiro — este app só "
                "enxerga quem já foi liberado para ele."
            )

        Usuario = get_user_model()
        usuario, criado = Usuario.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "first_name": (dados.get("nome") or "").split(" ")[0],
                "last_name": " ".join((dados.get("nome") or "").split(" ")[1:]),
                "is_active": True,
            },
        )
        if criado:
            usuario.set_unusable_password()
            usuario.save(update_fields=["password"])

        vinculo, _ = VinculoIdentidade.objects.get_or_create(
            usuario=usuario, defaults={"identidade_id": dados["identidade_id"]}
        )
        anterior = vinculo.papel
        vinculo.papel = papel
        # O save() do modelo sincroniza `is_staff` com o papel.
        vinculo.save()

        if papel:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{email}: {anterior or 'sem papel'} → {papel}"
                    + (" (acesso ao /django-admin/ liberado)"
                       if papel == Papel.DIRETORIA.value else "")
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"{email}: papel removido (era {anterior or 'nenhum'})")
            )
