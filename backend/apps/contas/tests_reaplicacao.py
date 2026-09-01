"""
Reaplicar o papel do Conecta ID por cima de um vínculo que já existe.

O papel é semente: lido uma vez, no primeiro acesso. É o que impede o login
seguinte de desfazer, em silêncio, uma promoção feita no /django-admin/ — e é
também o que deixava de fora quem já entrou. A reaplicação é a exceção pedida à
mão no admin do Conecta ID, e vale uma vez.
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
class ReaplicacaoTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.identidade_id = str(uuid.uuid4())
        # Primeiro acesso: a conta e o vínculo passam a existir, já com papel.
        self.entrar({"papel": Papel.GERENTE})

    def entrar(self, config=None, reaplicar=False, email="fulano@sejaap.com.br"):
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = {
                "identidade_id": self.identidade_id,
                "email": email,
                "nome": "Fulano de Tal",
                "precisa_trocar_senha": False,
                "config_do_app": config or {},
                "reaplicar_config": reaplicar,
            }
            return self.client.post(
                "/api/sessao", {"email": email, "senha": "segredo"}, format="json"
            )

    def papel(self):
        return VinculoIdentidade.objects.get(identidade_id=self.identidade_id).papel

    def test_com_a_marca_o_papel_e_reescrito(self):
        self.assertEqual(self.entrar({"papel": Papel.DIRETORIA}, True).status_code, 200)
        self.assertEqual(self.papel(), Papel.DIRETORIA)

    def test_promover_a_diretoria_leva_o_admin_junto(self):
        """`is_staff` é amarrado ao papel no `save` do vínculo.

        Reaplicar com `update_fields` passaria por fora dele, e a pessoa viraria
        diretoria sem conseguir entrar no /django-admin/.
        """
        self.entrar({"papel": Papel.DIRETORIA}, True)
        self.assertTrue(User.objects.get(email="fulano@sejaap.com.br").is_staff)

    def test_rebaixar_tira_o_admin_junto(self):
        self.entrar({"papel": Papel.DIRETORIA}, True)
        self.entrar({"papel": Papel.GERENTE}, True)
        self.assertFalse(User.objects.get(email="fulano@sejaap.com.br").is_staff)

    def test_sem_a_marca_nada_acontece(self):
        """Se o papel valesse a cada login, promover no /django-admin/ seria
        desfeito no acesso seguinte."""
        vinculo = VinculoIdentidade.objects.get(identidade_id=self.identidade_id)
        vinculo.papel = Papel.DIRETORIA
        vinculo.save()

        self.entrar({"papel": Papel.GERENTE})

        self.assertEqual(self.papel(), Papel.DIRETORIA)

    def test_papel_vazio_nao_rebaixa_ninguem(self):
        """O pedido diz "faça valer o que está escrito", e não há nada escrito.

        Tirar um papel continua sendo operação do /django-admin/, onde quem faz
        vê o que está fazendo.
        """
        self.entrar({}, True)
        self.assertEqual(self.papel(), Papel.GERENTE)

    def test_papel_desconhecido_mantem_o_que_esta(self):
        self.entrar({"papel": "chefao"}, True)
        self.assertEqual(self.papel(), Papel.GERENTE)
