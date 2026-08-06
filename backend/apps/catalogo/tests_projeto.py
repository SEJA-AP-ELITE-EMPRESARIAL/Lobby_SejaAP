"""
Guardas do projeto — o que costuma quebrar em silêncio.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient


class MigrationsTest(TestCase):
    def test_nao_ha_migration_pendente(self):
        """Modelo alterado sem `makemigrations` é erro que só aparece no deploy —
        e nesta casa as migrations são aplicadas à mão, então ninguém percebe."""
        saida = StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run", stdout=saida)
        except SystemExit:
            self.fail(f"Há mudança de modelo sem migration:\n{saida.getvalue()}")


class SuperficiePublicaTest(TestCase):
    """O Lobby é anônimo por natureza. Este teste existe para que continue sendo.

    O risco é concreto: o Django convida a espalhar `login_required`, e o DRF
    deste projeto está fechado por padrão (`DEFAULT_PERMISSION_CLASSES`). Basta
    alguém esquecer o `AllowAny` numa rota do fluxo de venda para o consultor em
    campo bater num 401 no meio de um cadastro.
    """

    def test_catalogo_e_a_unica_rota_anonima_de_leitura(self):
        resposta = APIClient().get("/api/catalogo")
        self.assertEqual(resposta.status_code, 200)

    def test_django_admin_nao_fica_em_barra_admin(self):
        """`/admin` é a tela da diretoria (admin.html), servida pelo nginx.

        Se o Django reivindicar essa rota, a tela de valores some.
        """
        self.assertEqual(APIClient().get("/admin/").status_code, 404)
        # 302 para o login — a rota existe onde deveria.
        self.assertIn(APIClient().get("/django-admin/").status_code, (301, 302))


class FormatoDeErroTest(TestCase):
    """Todo erro da API sai como {"erro": "..."}.

    O front exibe `d.erro` cru. Se o DRF devolver o `{"detail": ...}` dele, a
    tela cai na mensagem genérica de fallback e esconde o motivo real — que é
    justamente o que a pessoa precisa ler para saber o que fazer.
    """

    def test_401_sem_credencial_usa_a_chave_erro(self):
        resposta = APIClient().put("/api/catalogo", {"cats": []}, format="json")
        self.assertEqual(resposta.status_code, 401)
        self.assertIn("erro", resposta.json())
        self.assertNotIn("detail", resposta.json())

    def test_403_de_permissao_traz_a_mensagem_da_classe(self):
        from apps.catalogo.tests_escrita import cria_pessoa
        from apps.contas.models import Papel

        cliente = APIClient()
        cliente.force_authenticate(user=cria_pessoa("g@sejaap.com.br", Papel.GERENTE))
        resposta = cliente.put("/api/catalogo", {"cats": []}, format="json")

        self.assertEqual(resposta.status_code, 403)
        self.assertIn("Somente a diretoria", resposta.json()["erro"])

    def test_erro_nunca_e_cacheavel(self):
        resposta = APIClient().put("/api/catalogo", {"cats": []}, format="json")
        self.assertEqual(resposta["Cache-Control"], "no-store")
