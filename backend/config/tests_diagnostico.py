"""
`POST /api/diagnostico/erro-de-teste` (TSK-1142): só superusuário dispara a exceção.

A rota existe para conferir o GlitchTip depois do deploy. Aberta a mais gente,
qualquer conta encheria o projeto `lobby` de issues falsas. Por isso o teste
cobre as contas que passam em outras portas do Lobby — gerente, diretoria (que
é `is_staff` e entra no /django-admin/), staff avulso e a asserção do Conecta — e
prova que nenhuma chega à exceção.
"""
import uuid

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.contas.models import Papel, VinculoIdentidade

from .diagnostico import ErroDeTesteDoGlitchTip, ErroDeTesteView

ROTA = "/api/diagnostico/erro-de-teste"


def _pessoa(email, papel="", **campos):
    usuario = User.objects.create_user(username=email, email=email, **campos)
    usuario.set_unusable_password()
    usuario.save()
    VinculoIdentidade.objects.create(usuario=usuario, identidade_id=uuid.uuid4(), papel=papel)
    return User.objects.get(pk=usuario.pk)


class RotaErroDeTesteTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.superusuario = _pessoa("su@sejaap.com.br", is_superuser=True)
        cls.contas_barradas = {
            "sem papel": _pessoa("consultor@sejaap.com.br"),
            "gerente": _pessoa("gerente@sejaap.com.br", Papel.GERENTE),
            "diretoria": _pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA),
            "staff sem superusuário": _pessoa("staff@sejaap.com.br", is_staff=True),
        }

    def setUp(self):
        # O teto conta no cache, que o rollback do TestCase não desfaz.
        cache.clear()
        self.addCleanup(cache.clear)

    def _cliente(self, usuario=None):
        cliente = APIClient()
        if usuario is not None:
            token = RefreshToken.for_user(usuario).access_token
            cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return cliente

    def test_a_url_e_a_view_do_diagnostico(self):
        rota = resolve(ROTA)
        self.assertEqual(rota.url_name, "diagnostico-erro-de-teste")
        self.assertIs(rota.func.view_class, ErroDeTesteView)

    def test_diretoria_e_staff_de_verdade(self):
        """A diretoria entra no /django-admin/ (is_staff), mas não é superusuário."""
        diretoria = self.contas_barradas["diretoria"]
        self.assertTrue(diretoria.is_staff)
        self.assertFalse(diretoria.is_superuser)

    def test_sem_login_leva_401(self):
        with self.assertLogs("django.request", "WARNING"):
            self.assertEqual(self._cliente().post(ROTA).status_code, 401)

    def test_quem_nao_e_superusuario_leva_403_sem_disparar_a_excecao(self):
        for nome, usuario in self.contas_barradas.items():
            with self.subTest(conta=nome), self.assertLogs("django.request", "WARNING"):
                resposta = self._cliente(usuario).post(ROTA)
                self.assertEqual(resposta.status_code, 403)
                self.assertIn("superusuário", resposta.json()["erro"])

    def test_asercao_do_conecta_nao_alcanca_a_rota(self):
        """A rota não está na lista fechada da asserção: 403 antes de a view rodar."""
        with self.assertLogs("lobby.integracao", "ERROR"):
            resposta = self._cliente(self.superusuario).post(
                ROTA, HTTP_X_CONECTA_ASSERCAO="qualquer-coisa")
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(resposta.json()["codigo"], "ROTA_NAO_LIBERADA")

    def test_superusuario_dispara_a_excecao(self):
        with self.assertLogs("django.request", "ERROR"), self.assertRaises(ErroDeTesteDoGlitchTip):
            self._cliente(self.superusuario).post(ROTA)

    def test_superusuario_com_get_nao_dispara(self):
        with self.assertLogs("django.request", "WARNING"):
            self.assertEqual(self._cliente(self.superusuario).get(ROTA).status_code, 405)

    def test_teto_de_dez_por_hora_por_pessoa(self):
        cliente = self._cliente(self.superusuario)
        with self.assertLogs("django.request", "WARNING"):
            for _ in range(10):
                with self.assertRaises(ErroDeTesteDoGlitchTip):
                    cliente.post(ROTA)
            self.assertEqual(cliente.post(ROTA).status_code, 429)
            # O 403 de quem não pode não gasta a cota de ninguém.
            self.assertEqual(self._cliente(self.contas_barradas["gerente"]).post(ROTA).status_code, 403)
