"""
Expurgo do histórico de publicações da tabela de preços.

O QUE ISTO RESOLVE

O KV gravava uma cópia de cada publicação em `historico:<ISO>` com TTL de 90
dias — um backup que ninguém lia e que sumia sozinho, sem registro de que tinha
sumido. Ao trazer o histórico para o banco (`PublicacaoCatalogo`), a promessa
escrita no docstring do modelo foi a oposta: **o expurgo passa a ser explícito**,
um comando que alguém roda olhando, e não um relógio invisível.

Faltava o comando. Ele é este.

    manage.py expurgar_publicacoes --dias 365            # ensaio: só conta
    manage.py expurgar_publicacoes --dias 365 --confirmar # apaga de verdade

DUAS PROTEÇÕES QUE VALEM EXPLICAÇÃO

1. **Ensaio por padrão.** Sem `--confirmar` nada é apagado. Expurgo de trilha de
   auditoria é irreversível e roda em cron: o padrão seguro é o que não apaga.

2. **`--manter` é um piso, e vence a idade.** Guardar as N publicações mais
   recentes mesmo que todas sejam antigas. Sem isso, uma tabela que não muda há
   dois anos ficaria sem nenhuma publicação — e a última publicação não é só
   histórico: é dela que sai o `atualizadoEm` do `GET /api/catalogo` e o
   "Última alteração por X" da tela da diretoria. Apagar todas transformaria a
   tela num "—" que ninguém saberia explicar.

O QUE O COMANDO NÃO APAGA

A `Vigencia`. São coisas diferentes: a publicação é o EVENTO (pesado, carrega o
catálogo inteiro em JSON e é o que engorda a tabela), a vigência é o ESTADO
("12.997 valeu de agosto até hoje") e é o que responde "quanto custava em
março". Expurgar vigência é apagar a resposta, não o arquivo morto — se um dia
for preciso, que seja outro comando, com outra decisão por trás.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.catalogo.models import PublicacaoCatalogo

DIAS_PADRAO = 365
MANTER_PADRAO = 12


class Command(BaseCommand):
    help = (
        "Apaga publicações antigas da tabela de preços. Ensaio por padrão: "
        "sem --confirmar, apenas relata o que apagaria."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dias",
            type=int,
            default=DIAS_PADRAO,
            help=f"Idade a partir da qual a publicação é candidata (padrão: {DIAS_PADRAO}).",
        )
        parser.add_argument(
            "--manter",
            type=int,
            default=MANTER_PADRAO,
            help=(
                "Piso: quantas publicações mais recentes ficam de pé, mesmo "
                f"velhas (padrão: {MANTER_PADRAO}; mínimo 1)."
            ),
        )
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="Apaga de verdade. Sem isto, o comando só conta.",
        )

    def handle(self, *args, **opcoes):
        dias = opcoes["dias"]
        manter = opcoes["manter"]
        confirmar = opcoes["confirmar"]

        if dias < 1:
            raise CommandError("--dias tem que ser 1 ou mais.")
        if manter < 1:
            # Zero apagaria a última publicação junto, e com ela o `atualizadoEm`
            # da tela. Recusar é melhor do que corrigir em silêncio: quem digitou
            # 0 queria alguma coisa, e ela não é possível.
            raise CommandError(
                "--manter tem que ser 1 ou mais: a última publicação é a fonte "
                "do 'atualizadoEm' do catálogo e do 'Última alteração por' do painel."
            )

        corte = timezone.now() - timedelta(days=dias)
        total = PublicacaoCatalogo.objects.count()

        # As `manter` mais recentes ficam de pé por decreto; o resto só sai se
        # também for velho o bastante. Duas consultas em vez de um `exclude`
        # aninhado porque o SQLite recusa LIMIT dentro de subconsulta de DELETE.
        protegidas = list(
            PublicacaoCatalogo.objects.order_by("-publicado_em").values_list(
                "id", flat=True
            )[:manter]
        )
        candidatas = PublicacaoCatalogo.objects.filter(
            publicado_em__lt=corte
        ).exclude(id__in=protegidas)

        quantas = candidatas.count()
        self.stdout.write(
            f"{total} publicação(ões) no banco · corte em {corte:%d/%m/%Y} "
            f"({dias} dias) · piso de {manter}"
        )

        if not quantas:
            self.stdout.write(self.style.SUCCESS("Nada a expurgar."))
            return

        mais_velha = candidatas.order_by("publicado_em").first()
        mais_nova = candidatas.order_by("-publicado_em").first()
        self.stdout.write(
            f"Candidatas: {quantas} — de {mais_velha.publicado_em:%d/%m/%Y} "
            f"a {mais_nova.publicado_em:%d/%m/%Y}"
        )

        if not confirmar:
            self.stdout.write(
                self.style.WARNING(
                    "Ensaio: nada foi apagado. Repita com --confirmar para apagar."
                )
            )
            return

        apagadas, _ = candidatas.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"{apagadas} publicação(ões) apagada(s). "
                f"Restam {PublicacaoCatalogo.objects.count()}."
            )
        )
