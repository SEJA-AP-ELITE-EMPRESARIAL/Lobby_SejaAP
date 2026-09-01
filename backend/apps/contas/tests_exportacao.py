"""
`exportar_provisionamento` — a fotografia que o Conecta ID vai guardar.

Só leitura. Este app não pode escrever a própria configuração pela API (não tem
`pode_gerir_identidades`), e é por isso que o backfill dele passa por arquivo.
"""
import json
import uuid
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from .models import Papel, VinculoIdentidade


class ExportarProvisionamentoTest(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user("fulano", "fulano@sejaap.com.br")
        self.vinculo = VinculoIdentidade.objects.create(
            usuario=self.usuario, identidade_id=str(uuid.uuid4()), papel=Papel.GERENTE
        )

    def exportar(self, **opcoes):
        saida = StringIO()
        call_command("exportar_provisionamento", stdout=saida, stderr=StringIO(), **opcoes)
        return json.loads(saida.getvalue())

    def test_exporta_o_papel_com_o_app_no_arquivo(self):
        dados = self.exportar()
        self.assertEqual(dados["app"], "lobby")
        self.assertEqual(dados["itens"][0]["configuracao"], {"papel": Papel.GERENTE})
        self.assertEqual(dados["itens"][0]["email"], "fulano@sejaap.com.br")

    def test_quem_nao_tem_papel_fica_de_fora(self):
        """Sem papel não é um papel, é a ausência de um.

        A pessoa entra e não autoriza nada, e o admin do Conecta ID mostrando
        "—" para ela diz exatamente isso.
        """
        self.vinculo.papel = ""
        self.vinculo.save()
        self.assertEqual(self.exportar()["itens"], [])

    def test_desativado_sai_so_com_a_opcao(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])

        self.assertEqual(self.exportar()["itens"], [])
        self.assertEqual(len(self.exportar(incluir_inativos=True)["itens"]), 1)
