"""
O comprovante do DH - Avaliação (TSK-1056), testado pelo lado de quem burlaria.

O valor unitário é livre — o consultor o digita —, salvo o que a diretoria
fixar no /admin. A trava da tela é cosmética sozinha: o payload sai do
navegador. Estes testes garantem que o servidor só assina a modalidade fixa com
o valor fixo, e que os itens assinados são os que chegam ao n8n.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalogo.models import Categoria, Produto
from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel
from apps.dh_avaliacao.models import ConfiguracaoValores

from .models import ComprovanteVenda
from .tests_comprovante import N8N, venda_de_tabela


# 10 relatórios a 150 + 3 devolutivas a 450 = 1.500 + 1.350 = 2.850.
def venda_avaliacao(**mudancas):
    dados = {
        "fluxo": "dh_avaliacao",
        "categoria_id": "produtos",
        "produto_id": "avaliacoes",
        "negociado": False,
        "valor_total": 2850,
        "valor_mensal": None,
        "entrada": 2850,
        "cronograma": [],
        "protocolo": "LBA-261014312345",
        "itens": [
            {"modalidade": "relatorio", "quantidade": 10, "valor_unitario": 150},
            {"modalidade": "devolutiva", "quantidade": 3, "valor_unitario": 450},
        ],
    }
    dados.update(mudancas)
    return dados


def itens(relatorio=(10, 150), devolutiva=(3, 450)):
    out = []
    if relatorio:
        out.append({"modalidade": "relatorio", "quantidade": relatorio[0], "valor_unitario": relatorio[1]})
    if devolutiva:
        out.append({"modalidade": "devolutiva", "quantidade": devolutiva[0], "valor_unitario": devolutiva[1]})
    return out


def fixa(relatorio=None, devolutiva=None):
    """Grava a aba como a diretoria: `None` deixa a modalidade livre a 150/450."""
    return ConfiguracaoValores.objects.create(
        relatorio_valor=Decimal(str(relatorio or 150)),
        relatorio_fixo=relatorio is not None,
        devolutiva_valor=Decimal(str(devolutiva or 450)),
        devolutiva_fixo=devolutiva is not None,
    )


class Base(TestCase):
    def setUp(self):
        Produto.objects.create(
            categoria=Categoria.objects.get(slug="produtos"),
            slug="avaliacoes",
            nome="DH - Avaliação",
            sigla="LBA",
            fluxo="dh_avaliacao",
            ordem=1,
        )

    def emitir(self, dados, usuario=None):
        cliente = APIClient()
        if usuario:
            cliente.force_authenticate(user=usuario)
        return cliente.post("/api/venda/comprovante", dados, format="json")

    def assertRecusa(self, dados, trecho, usuario=None):
        resposta = self.emitir(dados, usuario=usuario)
        self.assertEqual(resposta.status_code, 422, resposta.json())
        self.assertIn(trecho, resposta.json()["erro"])
        self.assertFalse(ComprovanteVenda.objects.exists())


class ValorLivreTest(Base):
    def test_venda_com_os_valores_iniciais_e_assinada_sem_login(self):
        resposta = self.emitir(venda_avaliacao())
        self.assertEqual(resposta.status_code, 200, resposta.json())
        comprovante = ComprovanteVenda.objects.get()
        self.assertEqual(comprovante.fluxo, "dh_avaliacao")
        self.assertEqual(
            comprovante.valores["itens"],
            [
                {"modalidade": "relatorio", "quantidade": 10, "valor_unitario": "150.00"},
                {"modalidade": "devolutiva", "quantidade": 3, "valor_unitario": "450.00"},
            ],
        )

    def test_valor_livre_digitado_pelo_consultor_passa(self):
        """Livre é livre: o servidor não tem tabela contra a qual conferir."""
        resposta = self.emitir(
            venda_avaliacao(valor_total=1560, entrada=1560, itens=itens(relatorio=(8, 120), devolutiva=(1, 600)))
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())

    def test_uma_modalidade_so(self):
        resposta = self.emitir(venda_avaliacao(valor_total=900, entrada=900, itens=itens(relatorio=None, devolutiva=(2, 450))))
        self.assertEqual(resposta.status_code, 200, resposta.json())

    def test_centavo_de_arredondamento_e_tolerado(self):
        resposta = self.emitir(venda_avaliacao(valor_total=2849.99, entrada=2849.99))
        self.assertEqual(resposta.status_code, 200, resposta.json())


class ValorFixoTest(Base):
    def test_fixo_com_o_valor_fixo_passa(self):
        fixa(devolutiva=480)
        resposta = self.emitir(
            venda_avaliacao(valor_total=2940, entrada=2940, itens=itens(devolutiva=(3, 480)))
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())

    def test_fixo_com_outro_valor_e_recusado(self):
        """A burla central: a tela trava, o payload montado à mão não."""
        fixa(devolutiva=480)
        self.assertRecusa(venda_avaliacao(), "fixado pela diretoria em R$ 480,00")

    def test_nem_gerente_autorizando_passa_valor_diferente_do_fixo(self):
        fixa(devolutiva=480)
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        self.assertRecusa(venda_avaliacao(), "fixado pela diretoria", usuario=gerente)
        self.assertRecusa(venda_avaliacao(negociado=True), "não tem negociação", usuario=gerente)

    def test_o_fixo_de_uma_modalidade_nao_trava_a_outra(self):
        fixa(devolutiva=450)
        resposta = self.emitir(
            venda_avaliacao(valor_total=1950, entrada=1950, itens=itens(relatorio=(4, 150), devolutiva=(3, 450)))
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())
        resposta = self.emitir(
            venda_avaliacao(valor_total=2150, entrada=2150, itens=itens(relatorio=(4, 200), devolutiva=(3, 450)))
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())

    def test_vale_a_ultima_gravacao(self):
        fixa(devolutiva=480)
        fixa()  # a diretoria soltou de novo
        resposta = self.emitir(venda_avaliacao())
        self.assertEqual(resposta.status_code, 200, resposta.json())


class ItensQueNaoSeSustentamTest(Base):
    def test_total_que_nao_fecha_com_os_itens(self):
        self.assertRecusa(venda_avaliacao(valor_total=2000, entrada=2000), "não fecha")

    def test_entrada_que_nao_fecha_com_o_total(self):
        self.assertRecusa(venda_avaliacao(entrada=100), "não fecha")

    def test_sem_itens(self):
        self.assertRecusa(venda_avaliacao(itens=[], valor_total=0, entrada=0), "pelo menos uma")

    def test_itens_ausentes(self):
        dados = venda_avaliacao()
        del dados["itens"]
        self.assertRecusa(dados, "Itens inválidos")

    def test_quantidade_zero_nao_vai_nos_itens(self):
        self.assertRecusa(
            venda_avaliacao(valor_total=1500, entrada=1500, itens=itens(devolutiva=(0, 450))),
            "maior que zero",
        )

    def test_quantidade_fracionada(self):
        self.assertRecusa(
            venda_avaliacao(valor_total=1575, entrada=1575, itens=itens(relatorio=(10.5, 150), devolutiva=None)),
            "quantidade",
        )

    def test_quantidade_absurda(self):
        self.assertRecusa(
            venda_avaliacao(valor_total=150 * 20000, entrada=150 * 20000, itens=itens(relatorio=(20000, 150), devolutiva=None)),
            "fora da faixa",
        )

    def test_valor_unitario_zero(self):
        self.assertRecusa(
            venda_avaliacao(valor_total=1350, entrada=1350, itens=itens(relatorio=(10, 0))),
            "fora da faixa",
        )

    def test_valor_unitario_que_nao_e_numero(self):
        self.assertRecusa(venda_avaliacao(itens=itens(relatorio=(10, "NaN"))), "valor unitário")

    def test_modalidade_desconhecida(self):
        dados = venda_avaliacao()
        dados["itens"][0]["modalidade"] = "laudo"
        self.assertRecusa(dados, "desconhecida")

    def test_modalidade_repetida(self):
        self.assertRecusa(
            venda_avaliacao(
                valor_total=3000, entrada=3000,
                itens=itens(relatorio=(10, 150), devolutiva=None) * 2,
            ),
            "dois itens",
        )

    def test_produto_de_outro_fluxo_nao_entra_por_aqui(self):
        """O Recrutamento e Seleção (fluxo dh) e o Elite não viram valor livre."""
        self.assertRecusa(venda_avaliacao(produto_id="recrutamento-selecao"), "não encontrado")
        self.assertRecusa(venda_avaliacao(categoria_id="elite", produto_id="pro"), "não encontrado")


@override_settings(LOBBY_N8N_TOKEN=N8N)
class ValidacaoTest(Base):
    def validar(self, token, valores):
        return APIClient().post(
            "/api/venda/validar",
            {"comprovante": token, "valores": valores},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {N8N}",
        ).json()

    def test_comprovante_legitimo_e_aceito_pelo_n8n(self):
        dados = venda_avaliacao()
        token = self.emitir(dados).json()["comprovante"]
        corpo = self.validar(token, dados)
        self.assertTrue(corpo["valido"], corpo)
        self.assertEqual(corpo["fluxo"], "dh_avaliacao")
        self.assertEqual(corpo["protocolo"], "LBA-261014312345")

    def test_trocar_os_itens_mantendo_o_total_e_pego(self):
        """Mesma soma, outra distribuição: 10×150 + 3×450 = 2.850 = 19×150.

        É por isto que os itens entram no hash, e não só o total.
        """
        dados = venda_avaliacao()
        token = self.emitir(dados).json()["comprovante"]
        trocado = venda_avaliacao(itens=itens(relatorio=(19, 150), devolutiva=None))
        corpo = self.validar(token, trocado)
        self.assertFalse(corpo["valido"])
        self.assertEqual(corpo["motivo"], "valores_adulterados")


class OutrosFluxosNaoMudamTest(TestCase):
    def test_itens_nao_entram_no_que_o_elite_assina(self):
        """Acrescentar a chave em todo fluxo mudaria o hash das vendas de sempre."""
        resposta = APIClient().post(
            "/api/venda/comprovante", venda_de_tabela(itens=[{"modalidade": "x"}]), format="json"
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("itens", ComprovanteVenda.objects.get().valores)
