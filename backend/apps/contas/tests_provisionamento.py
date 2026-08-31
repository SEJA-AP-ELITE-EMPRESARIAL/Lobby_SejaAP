"""
O papel que vem do Conecta ID junto com o acesso.

O Lobby não tem tela de usuários: conceder acesso no Conecta ID é a ÚNICA porta,
e a conta local nasce no primeiro login. Era a metade que faltava — quem
concedia entregava uma conta que entra e não autoriza nada, e alguém tinha de ir
ao /django-admin/ promover depois, noutro sistema.

A regra que estes testes seguram é a que evita o estrago do outro lado: o papel
é **semente**, aplicada uma vez. Se valesse a cada login, promover ou rebaixar
alguém no /django-admin/ seria desfeito no acesso seguinte, sem aviso.
"""
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Papel, VinculoIdentidade

ALVO = "apps.contas.views.ClienteIdentidade"


@override_settings(AUTH_CENTRAL_ATIVO=True)
class ProvisionamentoTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        # O contador do throttle vive no cache, e o LocMemCache é do PROCESSO —
        # o rollback do TestCase não o desfaz.
        cache.clear()
        self.identidade_id = str(uuid.uuid4())

    def entrar(self, config=None, email="fulano@sejaap.com.br"):
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = {
                "identidade_id": self.identidade_id,
                "email": email,
                "nome": "Fulano de Tal",
                "precisa_trocar_senha": False,
                "config_do_app": config or {},
            }
            return self.client.post(
                "/api/sessao", {"email": email, "senha": "segredo"}, format="json"
            )

    def test_papel_vem_da_configuracao_do_acesso(self):
        self.assertEqual(self.entrar({"papel": Papel.GERENTE}).status_code, 200)
        vinculo = VinculoIdentidade.objects.get(identidade_id=self.identidade_id)
        self.assertEqual(vinculo.papel, Papel.GERENTE)

    def test_diretoria_ganha_o_admin_junto(self):
        """`is_staff` é amarrado ao papel no `save` do vínculo.

        Semear com `update_fields` passaria por fora dele, e a pessoa viraria
        diretoria sem conseguir entrar no /django-admin/ — que é justamente o
        que aquela amarração existe para evitar.
        """
        self.entrar({"papel": Papel.DIRETORIA})
        self.assertTrue(User.objects.get(email="fulano@sejaap.com.br").is_staff)

    def test_sem_configuracao_continua_nascendo_sem_papel(self):
        """Ter acesso ao Lobby significa "pode entrar", não "pode autorizar".

        Sem papel a sessão é recusada — e o certo continua sendo esse. O que a
        configuração muda é só o caso de quem foi provisionado: essa pessoa não
        precisa mais de uma segunda passagem pelo /django-admin/ antes do
        primeiro acesso funcionar.
        """
        self.assertEqual(self.entrar().status_code, 403)
        vinculo = VinculoIdentidade.objects.get(identidade_id=self.identidade_id)
        self.assertEqual(vinculo.papel, "")

    def test_papel_desconhecido_nao_vira_papel_inventado(self):
        """Fica sem papel, que é o mesmo estado de quem não foi promovido.

        A conta e o vínculo nascem do mesmo jeito, e a diretoria promove — o
        desfecho é o de antes da configuração existir, não um erro novo.
        """
        self.assertEqual(self.entrar({"papel": "chefao"}).status_code, 403)
        vinculo = VinculoIdentidade.objects.get(identidade_id=self.identidade_id)
        self.assertEqual(vinculo.papel, "")

    def test_configuracao_nao_reaplica_a_cada_login(self):
        self.entrar({"papel": Papel.GERENTE})
        vinculo = VinculoIdentidade.objects.get(identidade_id=self.identidade_id)
        vinculo.papel = Papel.DIRETORIA
        vinculo.save()

        self.entrar({"papel": Papel.GERENTE})
        vinculo.refresh_from_db()
        self.assertEqual(vinculo.papel, Papel.DIRETORIA)
