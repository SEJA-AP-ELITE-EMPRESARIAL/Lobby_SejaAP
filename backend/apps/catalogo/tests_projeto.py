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


class SuperficieAbertaTest(TestCase):
    """Desde a TSK-1513 o Lobby pede login. Este teste existe para que continue.

    Até 14/11/2026 o guarda daqui era o contrário (o catálogo TINHA de ser
    anônimo). A decisão do dono mudou: todo uso pede a conta do Conecta, e só
    ficam abertas as rotas de entrar e de senha — mais as que têm credencial
    própria: o `/api/venda/validar` (segredo do n8n), as de `/api/integracao/`
    (asserção do Conecta), o `/metrics` (token do Prometheus) e o `/saude` do
    healthcheck, fora de `/api/`.

    A varredura é pela tabela de rotas inteira, e não por uma lista escrita à mão:
    rota nova que nascer aberta sem ninguém decidir quebra aqui.
    """

    ABERTAS = {
        "abrir-sessao", "esqueci-senha", "definir-senha",
        "venda-validar", "metricas", "saude",
    }

    @staticmethod
    def _rotas(padroes, prefixo=""):
        from django.urls import URLResolver

        for padrao in padroes:
            if isinstance(padrao, URLResolver):
                yield from SuperficieAbertaTest._rotas(padrao.url_patterns, prefixo + str(padrao.pattern))
            else:
                yield prefixo + str(padrao.pattern), padrao

    def test_nenhuma_rota_responde_sem_sessao_fora_da_lista(self):
        import re

        from django.urls import get_resolver

        conferidas = 0
        for caminho, padrao in self._rotas(get_resolver().url_patterns):
            if caminho.startswith("django-admin/") or padrao.name in self.ABERTAS:
                continue
            visao = getattr(padrao.callback, "cls", None)
            self.assertIsNotNone(visao, f"/{caminho} não é view do DRF: decida se ela abre")
            url = "/" + re.sub(r"<[^>]+>", "x", caminho)
            for metodo in ("get", "post", "put", "patch", "delete"):
                if not hasattr(visao, metodo):
                    continue
                with self.subTest(rota=url, metodo=metodo.upper()):
                    resposta = getattr(APIClient(), metodo)(url, {}, format="json")
                    # 503 é a integração desligada (sem chave na suíte), que
                    # também não atende ninguém.
                    self.assertIn(resposta.status_code, (401, 503), resposta.content)
                    conferidas += 1
        # A varredura não pode passar por não ter achado nada.
        self.assertGreater(conferidas, 20)

    def test_o_que_o_consultor_usa_pede_sessao(self):
        """As rotas do contrato da TSK-1513, uma a uma, sem sessão: 401."""
        for rota in (
            "/api/catalogo", "/api/cobranca", "/api/departamentos",
            "/api/categorias-financeiras", "/api/dh-avaliacao/valores", "/api/sessao/atual",
        ):
            with self.subTest(rota=rota):
                self.assertEqual(APIClient().get(rota).status_code, 401)
        self.assertEqual(
            APIClient().post("/api/venda/comprovante", {}, format="json").status_code, 401
        )

    def test_entrar_e_senha_respondem_sem_sessao(self):
        """Corpo vazio: 400 da própria view, e não o 401 de quem não entrou."""
        with self.settings(AUTH_CENTRAL_ATIVO=True):
            for rota in ("/api/sessao", "/api/senha/esqueci", "/api/senha/definir"):
                with self.subTest(rota=rota):
                    self.assertEqual(APIClient().post(rota, {}, format="json").status_code, 400)

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
