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
from apps.contas.para_testes import cliente_logado, consultor, emite_comprovante, token_de

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


# Venda de DH (Recrutamento e Seleção): duas vagas, uma parcela por vaga.
# 2 × 4.000 (analista) + 1 × 7.000 (gerente) = 15.000 de adiantamento.
def venda_dh(**mudancas):
    dados = {
        "fluxo": "dh",
        "categoria_id": "produtos",
        "produto_id": "recrutamento-selecao",
        "negociado": False,
        "valor_total": 15000,
        "valor_mensal": None,
        "entrada": 0,
        "cronograma": [8000, 7000],
        "protocolo": "RES-260825012345",
    }
    dados.update(mudancas)
    return dados


class EmissaoTest(TestCase):
    """Quem vende está sempre logado (TSK-1513): `emitir` manda a sessão de um
    consultor sem papel e, quando há, o token de quem AUTORIZA no
    `X-Lobby-Autorizacao`."""

    def emitir(self, dados, autorizador=None):
        return emite_comprovante(dados, autorizador=autorizador)

    def test_venda_a_preco_de_tabela_e_assinada_sem_ninguem_autorizar(self):
        """O caso comum: consultor sem papel, valor da tabela. Tem que passar."""
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
            autorizador=gerente,
        )
        self.assertEqual(resposta.status_code, 200)
        c = ComprovanteVenda.objects.get()
        self.assertTrue(c.negociado)
        # Quem autorizou, e nunca quem está logado vendendo.
        self.assertEqual(c.autorizador_email, "gerente@sejaap.com.br")

    def test_conta_sem_papel_nao_autoriza_mesmo_logada(self):
        ninguem = cria_pessoa("novato@sejaap.com.br", "")
        resposta = self.emitir(
            venda_de_tabela(negociado=True, valor_total=1, valor_mensal=1,
                            entrada=1, cronograma=[]),
            autorizador=ninguem,
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

    def test_dh_assina_o_quadro_de_vagas(self):
        """O DH não tem tabela: o total é a soma dos adiantamentos do quadro.

        Uma parcela por vaga, entrada zero — é assim que o `index.html` monta o
        bloco assinado do Formulário DH.
        """
        resposta = self.emitir(venda_dh())
        self.assertEqual(resposta.status_code, 200)
        self.assertIn(".", resposta.json()["comprovante"])

    def test_dh_com_quadro_que_nao_fecha_e_recusado(self):
        """O resumo dizendo um número e o cronograma cobrando outro."""
        resposta = self.emitir(venda_dh(valor_total=9000))
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("não fecha", resposta.json()["erro"])

    def test_produto_de_tabela_nao_entra_como_dh(self):
        """A burla que o `fluxo=` do produto fecha.

        Sem a conferência, bastaria declarar `fluxo: "dh"` para vender um ELITE
        PRO por qualquer valor — o fluxo DH não confere preço, porque o produto
        dele não tem preço. Só produto marcado como de formulário passa por ali.
        """
        resposta = self.emitir(
            venda_dh(categoria_id="elite", produto_id="pro", valor_total=1, cronograma=[1])
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("não encontrado", resposta.json()["erro"])


def venda_negociada(**mudancas):
    return venda_de_tabela(negociado=True, valor_total=120000, valor_mensal=10000,
                           entrada=10000, cronograma=[10000] * 11, **mudancas)


class SessaoEAutorizacaoTest(TestCase):
    """Duas credenciais, dois cabeçalhos (TSK-1513).

    `Authorization` é a sessão de quem VENDE: obrigatória, e não autoriza nada
    sozinha. `X-Lobby-Autorizacao` é o token de quem AUTORIZA: lido como o
    `JWTOpcional` (ruim, vencido ou revogado = sem autorização), e é ele — nunca
    a sessão — que vai para `autorizado_por`.
    """

    def setUp(self):
        self.gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)

    def assertSemAutorizacao(self, resposta):
        self.assertEqual(resposta.status_code, 422, resposta.content)
        self.assertIn("exigem a autorização", resposta.json()["erro"])
        self.assertFalse(ComprovanteVenda.objects.exists())

    # ----- a sessão de quem vende -----------------------------------------

    def test_sessao_de_verdade_passa_pelo_jwt(self):
        """Sem `force_authenticate`: o token emitido pelo login vale aqui."""
        cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {token_de(consultor())}")
        resposta = cliente.post("/api/venda/comprovante", venda_de_tabela(), format="json")
        self.assertEqual(resposta.status_code, 200, resposta.content)

    def test_sessao_vencida_e_401_e_nada_e_assinado(self):
        """É o 401 que leva o lobby de volta ao login, e a venda para lá."""
        from rest_framework_simplejwt.tokens import AccessToken

        vencido = AccessToken.for_user(consultor())
        vencido.set_exp(lifetime=-timezone.timedelta(minutes=1))
        cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {vencido}")
        resposta = cliente.post("/api/venda/comprovante", venda_de_tabela(), format="json")
        self.assertEqual(resposta.status_code, 401)
        self.assertFalse(ComprovanteVenda.objects.exists())

    def test_so_o_token_do_gerente_sem_sessao_nao_basta(self):
        """O cabeçalho de autorização não substitui a sessão de quem vende."""
        resposta = APIClient().post(
            "/api/venda/comprovante", venda_negociada(), format="json",
            HTTP_X_LOBBY_AUTORIZACAO=f"Bearer {token_de(self.gerente)}",
        )
        self.assertEqual(resposta.status_code, 401)

    # ----- quem autoriza ---------------------------------------------------

    def test_autorizado_por_e_o_gerente_e_nunca_o_consultor(self):
        resposta = emite_comprovante(venda_negociada(), autorizador=self.gerente)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        comprovante = ComprovanteVenda.objects.get()
        self.assertEqual(comprovante.autorizador, self.gerente)
        self.assertEqual(comprovante.autorizador_email, "gerente@sejaap.com.br")

    def test_gerente_logado_nao_autoriza_so_por_estar_logado(self):
        """Autorizar é ação explícita: a sessão do gerente, sem o cabeçalho, é só
        a de quem vende."""
        self.assertSemAutorizacao(emite_comprovante(venda_negociada(), vendedor=self.gerente))

    def test_gerente_logado_autoriza_com_a_propria_sessao(self):
        """O "autorizar com a minha conta": o front manda o próprio token no
        `X-Lobby-Autorizacao`."""
        resposta = emite_comprovante(
            venda_negociada(), vendedor=self.diretoria, autorizador=self.diretoria
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(ComprovanteVenda.objects.get().autorizador_email, "diretoria@sejaap.com.br")

    def test_token_do_gerente_no_authorization_nao_autoriza(self):
        """Se o token do gerente chegar no lugar da sessão, ele é a sessão — e
        sessão não autoriza. Fecha a volta ao desenho de antes por engano."""
        cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {token_de(self.gerente)}")
        self.assertSemAutorizacao(
            cliente.post("/api/venda/comprovante", venda_negociada(), format="json")
        )

    def test_autorizacao_ruim_vira_sem_autorizacao_e_nao_401(self):
        """Ruim, sem `Bearer` ou vazia: 422 de "exigem a autorização", e a
        sessão de quem vende segue de pé (o 401 derrubaria o consultor)."""
        for cabecalho in ("Bearer lixo", "lixo", "Bearer", ""):
            with self.subTest(cabecalho=cabecalho):
                self.assertSemAutorizacao(
                    emite_comprovante(venda_negociada(), HTTP_X_LOBBY_AUTORIZACAO=cabecalho)
                )

    def test_autorizacao_vencida_vira_sem_autorizacao(self):
        from rest_framework_simplejwt.tokens import AccessToken

        vencido = AccessToken.for_user(self.gerente)
        vencido.set_exp(lifetime=-timezone.timedelta(minutes=1))
        self.assertSemAutorizacao(
            emite_comprovante(venda_negociada(), HTTP_X_LOBBY_AUTORIZACAO=f"Bearer {vencido}")
        )

    def test_autorizacao_ruim_nao_atrapalha_a_venda_de_tabela(self):
        resposta = emite_comprovante(venda_de_tabela(), HTTP_X_LOBBY_AUTORIZACAO="Bearer lixo")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertIsNone(ComprovanteVenda.objects.get().autorizador)

    def test_conta_sem_papel_no_cabecalho_nao_autoriza(self):
        resposta = emite_comprovante(venda_negociada(), autorizador=consultor("novato@sejaap.com.br"))
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("não tem permissão", resposta.json()["erro"])


@override_settings(LOBBY_N8N_TOKEN=N8N)
class ValidacaoTest(TestCase):
    """O lado do n8n."""

    def setUp(self):
        self.client = APIClient()

    def comprovante_de(self, dados, autorizador=None):
        return emite_comprovante(dados, autorizador=autorizador).json()["comprovante"]

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

    def test_emitir_comprovante_exige_a_sessao(self):
        """Era anônimo até a TSK-1513 (decisão do dono de 14/11/2026). Agora pede
        a sessão de quem vende — e só ela, sem papel."""
        resposta = APIClient().post(
            "/api/venda/comprovante", venda_de_tabela(), format="json"
        )
        self.assertEqual(resposta.status_code, 401)
        self.assertFalse(ComprovanteVenda.objects.exists())
        self.assertEqual(emite_comprovante(venda_de_tabela()).status_code, 200)
