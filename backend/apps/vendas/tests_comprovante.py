"""
A brecha que este app fecha, testada pelo lado de quem tentaria explorá-la.

Cada teste aqui é uma tentativa de burla concreta. Se algum deles passar a
falhar, alguém reabriu o buraco.
"""
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalogo.models import Produto
from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel, VinculoIdentidade

from .models import ComprovanteVenda

N8N = "segredo-do-n8n-para-teste"

# Venda legítima de ELITE PRO a preço de tabela: 12997 × 12 = 155964,
# com entrada de 12997 e 11 parcelas de 12997.
def venda_de_tabela(**mudancas):
    dados = {
        "fluxo": "elite",
        "categoria_id": "elite",
        "produto_id": "pro",
        "negociado": False,
        "valor_total": 155964,
        "valor_mensal": 12997,
        "entrada": 12997,
        "cronograma": [12997] * 11,
        "protocolo": "PRO-260806012345",
    }
    dados.update(mudancas)
    return dados


class EmissaoTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def emitir(self, dados, usuario=None):
        cliente = APIClient()
        if usuario:
            cliente.force_authenticate(user=usuario)
        return cliente.post("/api/venda/comprovante", dados, format="json")

    def test_venda_a_preco_de_tabela_e_assinada_sem_ninguem_logar(self):
        """O caso comum: consultor anônimo, valor da tabela. Tem que passar."""
        resposta = self.emitir(venda_de_tabela())
        self.assertEqual(resposta.status_code, 200)
        self.assertIn(".", resposta.json()["comprovante"])

    def test_valor_abaixo_da_tabela_sem_autorizacao_e_RECUSADO(self):
        """A burla central: baixar o preço sem passar por ninguém."""
        resposta = self.emitir(
            venda_de_tabela(valor_total=100000, valor_mensal=8000,
                            entrada=12000, cronograma=[8000] * 11)
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("não confere com a tabela", resposta.json()["erro"])

    def test_marcar_negociado_sem_credencial_nao_ajuda(self):
        """A saída óbvia depois de apanhar do teste acima: dizer que negociou.

        Sem credencial, dizer "foi negociado" não vale nada — que é o ponto.
        """
        resposta = self.emitir(
            venda_de_tabela(negociado=True, valor_total=100000, valor_mensal=8000,
                            entrada=12000, cronograma=[8000] * 11)
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("exigem a autorização", resposta.json()["erro"])

    def test_gerente_autoriza_valor_negociado(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        resposta = self.emitir(
            venda_de_tabela(negociado=True, valor_total=120000, valor_mensal=10000,
                            entrada=10000, cronograma=[10000] * 11),
            usuario=gerente,
        )
        self.assertEqual(resposta.status_code, 200)
        c = ComprovanteVenda.objects.get()
        self.assertTrue(c.negociado)
        self.assertEqual(c.autorizador_email, "gerente@sejaap.com.br")

    def test_conta_sem_papel_nao_autoriza_mesmo_logada(self):
        ninguem = cria_pessoa("novato@sejaap.com.br", "")
        resposta = self.emitir(
            venda_de_tabela(negociado=True, valor_total=1, valor_mensal=1,
                            entrada=1, cronograma=[]),
            usuario=ninguem,
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("não tem permissão", resposta.json()["erro"])

    def test_soma_das_parcelas_tem_que_fechar_com_o_total(self):
        """Declarar total de tabela e cobrar outra coisa nas parcelas."""
        resposta = self.emitir(venda_de_tabela(cronograma=[1] * 11))
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("não fecha com o valor total", resposta.json()["erro"])

    def test_produto_inexistente_e_recusado(self):
        resposta = self.emitir(venda_de_tabela(produto_id="inventado"))
        self.assertEqual(resposta.status_code, 422)

    def test_diferenca_de_centavo_e_tolerada(self):
        """O front calcula em float; exigir igualdade exata reprovaria venda boa."""
        resposta = self.emitir(venda_de_tabela(valor_total=155963.99))
        self.assertEqual(resposta.status_code, 200)

    def test_apn_tem_valor_livre_mas_ganha_comprovante(self):
        """Decisão de produto: a APN não tem tabela. O comprovante atesta origem."""
        resposta = self.emitir({
            "fluxo": "apn", "categoria_id": "apn", "produto_id": "",
            "negociado": False, "valor_total": 37000, "entrada": 37000,
            "cronograma": [], "protocolo": "APN-260806012345",
        })
        self.assertEqual(resposta.status_code, 200)

    def test_apn_com_categoria_errada_e_recusada(self):
        resposta = self.emitir({
            "fluxo": "apn", "categoria_id": "elite", "produto_id": "",
            "negociado": False, "valor_total": 1, "entrada": 1,
            "cronograma": [], "protocolo": "APN-1",
        })
        self.assertEqual(resposta.status_code, 422)


@override_settings(LOBBY_N8N_TOKEN=N8N)
class ValidacaoTest(TestCase):
    """O lado do n8n."""

    def setUp(self):
        self.client = APIClient()

    def comprovante_de(self, dados, usuario=None):
        cliente = APIClient()
        if usuario:
            cliente.force_authenticate(user=usuario)
        return cliente.post(
            "/api/venda/comprovante", dados, format="json"
        ).json()["comprovante"]

    def validar(self, token, valores, auth=f"Bearer {N8N}"):
        return APIClient().post(
            "/api/venda/validar",
            {"comprovante": token, "valores": valores},
            format="json",
            HTTP_AUTHORIZATION=auth,
        )

    def test_comprovante_legitimo_e_aceito(self):
        dados = venda_de_tabela()
        resposta = self.validar(self.comprovante_de(dados), dados)
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["valido"])
        self.assertEqual(corpo["protocolo"], "PRO-260806012345")
        self.assertFalse(corpo["negociado"])

    def test_valores_adulterados_no_caminho_sao_pegos(self):
        """Emitir a preço de tabela e trocar o valor antes de postar no n8n.

        É a burla mais provável de todas, porque o payload passa pelo navegador
        DEPOIS de assinado.
        """
        dados = venda_de_tabela()
        token = self.comprovante_de(dados)

        adulterado = venda_de_tabela(valor_total=1000)
        corpo = self.validar(token, adulterado).json()
        self.assertFalse(corpo["valido"])
        self.assertEqual(corpo["motivo"], "valores_adulterados")

    def test_comprovante_nao_pode_ser_reusado(self):
        dados = venda_de_tabela()
        token = self.comprovante_de(dados)

        self.assertTrue(self.validar(token, dados).json()["valido"])
        segunda = self.validar(token, dados).json()
        self.assertFalse(segunda["valido"])
        self.assertEqual(segunda["motivo"], "comprovante_ja_usado")

    def test_assinatura_forjada_e_recusada(self):
        dados = venda_de_tabela()
        identificador = self.comprovante_de(dados).split(".")[0]
        corpo = self.validar(f"{identificador}.{'0' * 64}", dados).json()
        self.assertFalse(corpo["valido"])
        self.assertEqual(corpo["motivo"], "assinatura_invalida")

    def test_comprovante_inventado_e_recusado(self):
        corpo = self.validar(f"{uuid.uuid4()}.{'0' * 64}", venda_de_tabela()).json()
        self.assertFalse(corpo["valido"])
        self.assertEqual(corpo["motivo"], "comprovante_desconhecido")

    def test_sem_comprovante_e_recusado(self):
        corpo = self.validar("", venda_de_tabela()).json()
        self.assertFalse(corpo["valido"])
        self.assertEqual(corpo["motivo"], "comprovante_ausente")

    def test_comprovante_expirado_e_recusado(self):
        dados = venda_de_tabela()
        token = self.comprovante_de(dados)
        ComprovanteVenda.objects.update(
            expira_em=timezone.now() - timezone.timedelta(minutes=1)
        )
        corpo = self.validar(token, dados).json()
        self.assertFalse(corpo["valido"])
        self.assertEqual(corpo["motivo"], "comprovante_expirado")

    def test_n8n_sem_o_segredo_nao_consome_nonce(self):
        """Sem isto, qualquer um queimaria os comprovantes das vendas alheias."""
        dados = venda_de_tabela()
        token = self.comprovante_de(dados)

        self.assertEqual(self.validar(token, dados, auth="Bearer errado").status_code, 401)
        self.assertEqual(self.validar(token, dados, auth="").status_code, 401)
        # E o comprovante continua valendo para o n8n de verdade.
        self.assertTrue(self.validar(token, dados).json()["valido"])

    def test_a_validacao_registra_quem_autorizou(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        dados = venda_de_tabela(negociado=True, valor_total=120000,
                                valor_mensal=10000, entrada=10000,
                                cronograma=[10000] * 11)
        corpo = self.validar(self.comprovante_de(dados, gerente), dados).json()
        self.assertTrue(corpo["valido"])
        self.assertTrue(corpo["negociado"])
        self.assertEqual(corpo["autorizado_por"], "gerente@sejaap.com.br")
        self.assertEqual(corpo["papel"], "gerente")


class ConfiguracaoTest(TestCase):
    @override_settings(LOBBY_N8N_TOKEN="")
    def test_sem_token_configurado_a_validacao_responde_503(self):
        """Falha visível: o n8n cai no ramo de conferência manual, não aceita calado."""
        resposta = APIClient().post(
            "/api/venda/validar", {"comprovante": "x.y"}, format="json"
        )
        self.assertEqual(resposta.status_code, 503)

    def test_emitir_comprovante_nao_exige_login(self):
        """O lobby é anônimo. Isto não pode mudar por causa desta feature."""
        resposta = APIClient().post(
            "/api/venda/comprovante", venda_de_tabela(), format="json"
        )
        self.assertEqual(resposta.status_code, 200)
