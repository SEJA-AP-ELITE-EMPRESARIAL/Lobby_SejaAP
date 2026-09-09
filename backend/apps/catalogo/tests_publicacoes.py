"""
A trilha de publicações: quem mexeu na tabela, quando, e o que mudou.

Cobre as duas metades que faltavam ao `PublicacaoCatalogo` — ele existia desde a
migração do KV, mas só era legível pelo `/django-admin/` e crescia para sempre:

    GET /api/publicacoes            a leitura para a tela da diretoria
    manage.py expurgar_publicacoes  o expurgo explícito que o modelo promete

O ponto sensível do endpoint é o que ele NÃO devolve: o campo `catalogo` é o
catálogo inteiro por linha. Mandá-lo numa lista de 100 seria trocar uma tela por
um download.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.contas.models import Papel

from .models import PublicacaoCatalogo
from .tests_escrita import cria_pessoa


class BasePublicacoes(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)

    def como(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    def publica(self, **campos):
        """Uma publicação já gravada, sem passar pelo PUT.

        Os testes daqui não estão medindo a publicação (isso é o
        `tests_escrita.py`), e sim a leitura e o expurgo — então vale montar a
        linha direto, inclusive com data no passado, que o PUT não permitiria.
        """
        publicacao = PublicacaoCatalogo.objects.create(
            autor=campos.get("autor", self.diretoria),
            autor_email=campos.get("autor_email", "diretoria@sejaap.com.br"),
            catalogo=campos.get("catalogo", [{"id": "elite", "products": []}]),
            resumo=campos.get("resumo", ""),
        )
        if "publicado_em" in campos:
            # `auto_now_add` ignora o valor passado ao create: só um update
            # posterior consegue envelhecer a linha.
            PublicacaoCatalogo.objects.filter(pk=publicacao.pk).update(
                publicado_em=campos["publicado_em"]
            )
            publicacao.refresh_from_db()
        return publicacao


class LeituraTest(BasePublicacoes):
    def test_anonimo_nao_le(self):
        self.assertEqual(self.client.get("/api/publicacoes").status_code, 401)

    def test_gerente_nao_le(self):
        """Gerente autoriza desconto numa venda; a série histórica de preço da
        empresa é outro alcance — o mesmo corte do `/api/historico`."""
        self.assertEqual(self.como(self.gerente).get("/api/publicacoes").status_code, 403)

    def test_diretoria_le_data_autor_e_alteracoes(self):
        self.publica(resumo="ELITE PRO: 11500.00 → 12997.00\nELITE EVO: 30000 → 31000")

        dados = self.como(self.diretoria).get("/api/publicacoes").json()

        self.assertEqual(dados["total"], 1)
        (registro,) = dados["publicacoes"]
        self.assertEqual(registro["autor"], "diretoria@sejaap.com.br")
        self.assertEqual(
            registro["alteracoes"],
            ["ELITE PRO: 11500.00 → 12997.00", "ELITE EVO: 30000 → 31000"],
        )
        self.assertTrue(registro["publicado_em"])

    def test_nao_devolve_o_catalogo_publicado(self):
        """O blob é o catálogo inteiro por linha: numa lista, viraria download."""
        self.publica(catalogo=[{"id": "elite", "products": [{"id": "pro"}]}])

        (registro,) = self.como(self.diretoria).get("/api/publicacoes").json()["publicacoes"]

        self.assertNotIn("catalogo", registro)

    def test_sem_autor_devolve_none(self):
        """A semente do catálogo não tem pessoa por trás. A tela mostra 'sistema'."""
        self.publica(autor=None, autor_email="")

        (registro,) = self.como(self.diretoria).get("/api/publicacoes").json()["publicacoes"]

        self.assertIsNone(registro["autor"])

    def test_publicacao_sem_alteracao_aparece_com_lista_vazia(self):
        """Publicar sem mudar valor nenhum não gera vigência — e é justamente o
        que esta tela mostra e a linha do tempo não."""
        self.publica(resumo="")

        (registro,) = self.como(self.diretoria).get("/api/publicacoes").json()["publicacoes"]

        self.assertEqual(registro["alteracoes"], [])

    def test_mais_recente_primeiro(self):
        agora = timezone.now()
        self.publica(resumo="velha", publicado_em=agora - timedelta(days=10))
        self.publica(resumo="nova", publicado_em=agora - timedelta(days=1))

        dados = self.como(self.diretoria).get("/api/publicacoes").json()

        self.assertEqual(
            [p["alteracoes"][0] for p in dados["publicacoes"]], ["nova", "velha"]
        )

    def test_limite_recorta_e_tem_teto(self):
        for i in range(4):
            self.publica(resumo=f"n{i}")

        cliente = self.como(self.diretoria)
        self.assertEqual(cliente.get("/api/publicacoes?limite=2").json()["total"], 2)
        # Lixo no parâmetro cai no padrão em vez de estourar 500 na tela.
        self.assertEqual(cliente.get("/api/publicacoes?limite=xis").json()["total"], 4)
        self.assertEqual(cliente.get("/api/publicacoes?limite=99999").json()["total"], 4)

    def test_nao_cacheia(self):
        """Atrás da Cloudflare, trilha de auditoria cacheada é trilha mentindo."""
        resposta = self.como(self.diretoria).get("/api/publicacoes")

        self.assertEqual(resposta["Cache-Control"], "no-store")


class ExpurgoTest(BasePublicacoes):
    def rodar(self, *args):
        saida = StringIO()
        call_command("expurgar_publicacoes", *args, stdout=saida, stderr=saida)
        return saida.getvalue()

    def envelhecidas(self, quantas, dias):
        agora = timezone.now()
        return [
            self.publica(resumo=f"n{i}", publicado_em=agora - timedelta(days=dias + i))
            for i in range(quantas)
        ]

    def test_ensaio_nao_apaga(self):
        self.envelhecidas(5, dias=400)

        saida = self.rodar("--dias", "365", "--manter", "1")

        self.assertEqual(PublicacaoCatalogo.objects.count(), 5)
        self.assertIn("Ensaio", saida)

    def test_confirmar_apaga_as_velhas(self):
        self.envelhecidas(3, dias=400)
        nova = self.publica(resumo="de hoje")

        self.rodar("--dias", "365", "--manter", "1", "--confirmar")

        self.assertEqual(
            list(PublicacaoCatalogo.objects.values_list("pk", flat=True)), [nova.pk]
        )

    def test_manter_e_um_piso_que_vence_a_idade(self):
        """Todas velhas: o piso é o que impede a tabela de ficar vazia — e com
        ela o `atualizadoEm` do catálogo e o 'Última alteração por' do painel."""
        self.envelhecidas(5, dias=400)

        self.rodar("--dias", "365", "--manter", "2", "--confirmar")

        self.assertEqual(PublicacaoCatalogo.objects.count(), 2)

    def test_manter_guarda_as_mais_recentes(self):
        velhas = self.envelhecidas(4, dias=400)  # n0 é a mais nova das quatro

        self.rodar("--dias", "365", "--manter", "1", "--confirmar")

        self.assertEqual(
            list(PublicacaoCatalogo.objects.values_list("pk", flat=True)),
            [velhas[0].pk],
        )

    def test_nao_toca_no_que_e_novo(self):
        self.publica(resumo="de hoje")

        saida = self.rodar("--dias", "365", "--confirmar")

        self.assertEqual(PublicacaoCatalogo.objects.count(), 1)
        self.assertIn("Nada a expurgar", saida)

    def test_manter_zero_e_recusado(self):
        """Zero apagaria a última publicação junto. Recusar é melhor do que
        corrigir em silêncio: quem digitou 0 queria algo que não é possível."""
        with self.assertRaises(CommandError):
            self.rodar("--dias", "365", "--manter", "0", "--confirmar")

    def test_dias_zero_e_recusado(self):
        with self.assertRaises(CommandError):
            self.rodar("--dias", "0", "--confirmar")

    def test_expurgo_nao_toca_na_linha_do_tempo(self):
        """Publicação é o arquivo morto (pesado); vigência é a resposta a 'quanto
        custava em março'. O comando apaga uma e não a outra."""
        from .models import Vigencia

        # A semente do catálogo já abre uma vigência por produto, e a tabela só
        # admite UMA aberta por (chave, campo) — daí a chave inventada aqui.
        Vigencia.objects.create(
            chave="produto-de-teste",
            rotulo="PRODUTO DE TESTE",
            campo=Vigencia.Campo.MENSALIDADE,
            valor=Decimal("12997.00"),
            vigente_de=timezone.now() - timedelta(days=500),
        )
        antes = Vigencia.objects.count()
        self.envelhecidas(3, dias=400)

        self.rodar("--dias", "365", "--manter", "1", "--confirmar")

        self.assertEqual(PublicacaoCatalogo.objects.count(), 1)
        self.assertEqual(Vigencia.objects.count(), antes)
