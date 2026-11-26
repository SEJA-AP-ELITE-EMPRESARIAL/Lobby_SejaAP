"""
O comprovante do Casais de Alta Performance (TSK-1622), pelo lado de quem burlaria.

O valor é um de dois fixos: R$ 39.997,00 (cheio) ou R$ 35.000,00 (Elite
Empresarial). Na PJ quem diz qual vale é o Conecta, e o booleano que a tela manda
não vale nada sozinho: o servidor pergunta de novo e só assina o valor do Elite
numa PJ que o Conecta disse ser ativa — ou, desde a TSK-1640, marcado à mão pelo
consultor quando o Conecta fica sem resposta também para o servidor. Na PF o
consultor decide.

E a validação do n8n (`/api/venda/validar`) recusa valor fora dos dois, o valor
do Elite trocado num comprovante do cheio, o CNPJ trocado, e o Elite de uma PJ
(conferido ou marcado à mão) que o Conecta, reconsultado, diz que não é ativa.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.casais.conecta import SemResposta
from apps.catalogo.models import Produto
from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel
from apps.contas.para_testes import cliente_logado, consultor, emite_comprovante

from .models import ComprovanteVenda
from .tests_comprovante import N8N, venda_de_tabela

ALVO = "apps.casais.servicos.conecta.cliente_ativo"
ATIVO = "22.122.190/0001-52"
INATIVO = "37.598.418/0001-52"
FORA = "63.580.153/0001-75"  # o Conecta não responde por este
CPF = "775.927.214-65"


def conecta_falso(cnpj):
    if cnpj == "63580153000175":
        raise SemResposta("indisponivel")
    return cnpj == "22122190000152"


def venda_casais(tipo="PJ", documento=ATIVO, tabela="elite", conferido=True, **mudancas):
    valor = 35000 if tabela == "elite" else 39997
    dados = {
        "fluxo": "casais",
        "categoria_id": "produtos",
        "produto_id": "casais-alta-performance",
        "negociado": False,
        "valor_total": valor,
        "valor_mensal": None,
        "entrada": valor,
        "cronograma": [],
        "protocolo": "CAP-261125112345",
        "casais": {
            "tipo_pessoa": tipo,
            "documento": documento,
            "tabela": tabela,
            "elite_conferido_no_conecta": conferido,
        },
    }
    dados.update(mudancas)
    return dados


def venda_pf(tabela="cheio", documento=CPF, **mudancas):
    return venda_casais(tipo="PF", documento=documento, tabela=tabela, conferido=False, **mudancas)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        mock = patch(ALVO, side_effect=conecta_falso)
        self.conecta = mock.start()
        self.addCleanup(mock.stop)

    def emitir(self, dados, autorizador=None):
        return emite_comprovante(dados, autorizador=autorizador)

    def assertAssina(self, dados):
        resposta = self.emitir(dados)
        self.assertEqual(resposta.status_code, 200, resposta.json())
        return resposta.json()["comprovante"]

    def assertRecusa(self, dados, trecho, autorizador=None):
        resposta = self.emitir(dados, autorizador=autorizador)
        self.assertEqual(resposta.status_code, 422, resposta.json())
        self.assertIn(trecho, resposta.json()["erro"])
        self.assertFalse(ComprovanteVenda.objects.exists())


class ProdutoTest(TestCase):
    def test_o_produto_existe_pela_migration(self):
        produto = Produto.objects.get(categoria__slug="produtos", slug="casais-alta-performance")
        self.assertEqual((produto.sigla, produto.fluxo), ("CAP", "casais"))


class PessoaFisicaTest(Base):
    def test_os_dois_valores_passam_e_o_conecta_nao_e_perguntado(self):
        """Na PF, "paga o valor do Elite Empresarial" é do consultor."""
        for tabela in ("cheio", "elite"):
            with self.subTest(tabela=tabela):
                ComprovanteVenda.objects.all().delete()
                self.assertAssina(venda_pf(tabela=tabela))
        self.conecta.assert_not_called()

    def test_o_que_e_assinado(self):
        self.assertAssina(venda_pf(tabela="elite"))
        comprovante = ComprovanteVenda.objects.get()
        self.assertEqual(comprovante.fluxo, "casais")
        self.assertFalse(comprovante.negociado)
        self.assertEqual(comprovante.valores["valor_total"], "35000.00")
        # Na PF o CPF não fica no comprovante: não decide o valor.
        self.assertEqual(comprovante.valores["casais"], {
            "tipo_pessoa": "PF",
            "documento": "",
            "tabela": "elite",
            "elite_conferido_no_conecta": False,
        })

    def test_pf_nao_diz_que_o_conecta_conferiu(self):
        self.assertRecusa(
            venda_casais(tipo="PF", documento=CPF, tabela="elite", conferido=True),
            "não é conferido no Conecta",
        )

    def test_o_cpf_nao_e_assinado(self):
        """Outro CPF na validação não é adulteração: na PF o documento não entra."""
        token = self.assertAssina(venda_pf(tabela="cheio"))
        with override_settings(LOBBY_N8N_TOKEN=N8N):
            corpo = APIClient().post(
                "/api/venda/validar",
                {"comprovante": token, "valores": venda_pf(tabela="cheio", documento="863.645.391-73")},
                format="json", HTTP_AUTHORIZATION=f"Bearer {N8N}",
            ).json()
        self.assertTrue(corpo["valido"], corpo)


class ValorFixoTest(Base):
    def test_valor_fora_dos_dois_e_recusado(self):
        for valor in (30000, 39996.99, 35000.01, 0, 79994):
            with self.subTest(valor=valor):
                self.assertRecusa(venda_pf(valor_total=valor, entrada=valor),
                                  "R$ 39.997,00 (preço cheio) ou R$ 35.000,00 (Elite Empresarial)")

    def test_valor_tem_de_ser_o_da_tabela(self):
        self.assertRecusa(venda_pf(tabela="cheio", valor_total=35000, entrada=35000),
                          "não confere com a tabela")
        self.assertRecusa(venda_pf(tabela="elite", valor_total=39997, entrada=39997),
                          "não confere com a tabela")

    def test_sem_negociacao_nem_com_gerente(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        self.assertRecusa(venda_pf(negociado=True), "não tem negociação", autorizador=gerente)

    def test_um_pagamento_so(self):
        self.assertRecusa(venda_pf(entrada=19997, cronograma=[20000]), "sem cronograma")
        self.assertRecusa(venda_pf(entrada=0), "sem cronograma")

    def test_bloco_do_casais_e_obrigatorio_e_conferido(self):
        for casais, trecho in (
            (None, "ausentes"),
            ("PF", "ausentes"),
            ({"tipo_pessoa": "PX", "documento": CPF, "tabela": "cheio",
              "elite_conferido_no_conecta": False}, "Tipo de pessoa"),
            ({"tipo_pessoa": "PF", "documento": CPF, "tabela": "meia",
              "elite_conferido_no_conecta": False}, "Tabela"),
            ({"tipo_pessoa": "PF", "documento": CPF, "tabela": "cheio",
              "elite_conferido_no_conecta": "false"}, "Conferência do Conecta"),
        ):
            with self.subTest(casais=casais):
                self.assertRecusa(venda_pf(casais=casais), trecho)

    def test_produto_que_nao_e_o_casais(self):
        self.assertRecusa(venda_pf(produto_id="avaliacoes"), "não encontrado")
        self.assertRecusa(venda_pf(produto_id="recrutamento-selecao"), "não encontrado")

    def test_o_casais_nao_se_vende_pela_tabela_do_elite(self):
        resposta = emite_comprovante(venda_de_tabela(
            categoria_id="produtos", produto_id="casais-alta-performance", valor_total=35000,
            valor_mensal=None, entrada=35000, cronograma=[],
        ))
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("agora tem formulário próprio", resposta.json()["erro"])


class PessoaJuridicaTest(Base):
    def test_elite_com_o_cnpj_ativo_no_conecta(self):
        self.assertAssina(venda_casais(documento=ATIVO, tabela="elite", conferido=True))
        self.conecta.assert_called_once_with("22122190000152")
        self.assertEqual(ComprovanteVenda.objects.get().valores["casais"]["documento"], "22122190000152")

    def test_cheio_com_o_cnpj_nao_ativo(self):
        self.assertAssina(venda_casais(documento=INATIVO, tabela="cheio", conferido=True))

    def test_cheio_sem_resposta_do_conecta(self):
        self.assertAssina(venda_casais(documento=FORA, tabela="cheio", conferido=False))

    def test_elite_com_o_cnpj_que_nao_e_ativo_e_recusado(self):
        """O booleano da tela não vale nada sozinho: o servidor pergunta de novo."""
        self.assertRecusa(
            venda_casais(documento=INATIVO, tabela="elite", conferido=True),
            "não tem este CNPJ como cliente ativo do Elite Empresarial: o valor desta venda é R$ 39.997,00",
        )

    def test_sem_resposta_do_conecta_o_consultor_marca_a_mao(self):
        """TSK-1640: Conecta fora, lento, 429 ou 503 — as duas tabelas, sem conferência."""
        for motivo in ("indisponivel", "limite", "manutencao"):
            for tabela in ("elite", "cheio"):
                with self.subTest(motivo=motivo, tabela=tabela):
                    cache.clear()
                    ComprovanteVenda.objects.all().delete()
                    self.conecta.side_effect = SemResposta(motivo)
                    self.assertAssina(venda_casais(documento=ATIVO, tabela=tabela, conferido=False))
                    self.assertEqual(ComprovanteVenda.objects.get().valores["casais"], {
                        "tipo_pessoa": "PJ",
                        "documento": "22122190000152",
                        "tabela": tabela,
                        "elite_conferido_no_conecta": False,
                    })

    def test_o_elite_a_mao_vale_so_se_o_servidor_tambem_ficar_sem_resposta(self):
        """O navegador dizer que o Conecta estava fora não basta: o servidor pergunta.

        É o "Como confirmo" 4 da TSK-1640 na emissão: marcado à mão, mas o Conecta
        responde que não é ativa — recusa, com o valor que vale.
        """
        self.assertRecusa(
            venda_casais(documento=INATIVO, tabela="elite", conferido=False),
            "não tem este CNPJ como cliente ativo do Elite Empresarial: o valor desta venda é R$ 39.997,00",
        )
        self.conecta.assert_called_once_with("37598418000152")

    def test_marcado_a_mao_num_cliente_que_o_conecta_confirma(self):
        """Mesmo valor, mas a origem tem de ser a verdadeira: o Conecta respondeu."""
        self.assertRecusa(
            venda_casais(documento=ATIVO, tabela="elite", conferido=False),
            "O Conecta confirmou que este CNPJ é cliente ativo",
        )

    def test_sem_resposta_a_tela_nao_diz_que_o_conecta_conferiu(self):
        self.conecta.side_effect = SemResposta("indisponivel")
        for tabela in ("elite", "cheio"):
            with self.subTest(tabela=tabela):
                cache.clear()
                self.assertRecusa(
                    venda_casais(documento=ATIVO, tabela=tabela, conferido=True),
                    'o que você marcar em "paga o valor do Elite Empresarial"',
                )

    @override_settings(LOBBY_CASAIS_CONSULTAS_POR_HORA=1)
    def test_o_teto_da_pessoa_nao_abre_a_caixa(self):
        """Esgotar o próprio teto não vira o jeito de vender o Elite sem o Conecta."""
        self.assertAssina(venda_casais(documento=INATIVO, tabela="cheio", conferido=True))
        resposta = emite_comprovante(
            venda_casais(documento=ATIVO, tabela="elite", conferido=False, protocolo="CAP-261125112346")
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("muitas consultas ao Conecta", resposta.json()["erro"])
        self.assertIn("fica no preço cheio, R$ 39.997,00", resposta.json()["erro"])
        # O cheio, sem conferência, passa como antes.
        self.assertAssina(
            venda_casais(documento=ATIVO, tabela="cheio", conferido=False, protocolo="CAP-261125112347")
        )
        self.assertEqual(self.conecta.call_count, 1)

    def test_cheio_num_cliente_ativo_e_recusado(self):
        """É o valor do Elite que vale no contrato de quem é cliente (decisão do dono)."""
        self.assertRecusa(
            venda_casais(documento=ATIVO, tabela="cheio", conferido=False),
            "o valor desta venda é R$ 35.000,00",
        )

    def test_a_conferencia_tem_de_dizer_o_mesmo_que_o_servidor(self):
        self.assertRecusa(venda_casais(documento=INATIVO, tabela="cheio", conferido=False),
                          "o valor desta venda é R$ 39.997,00")
        self.assertRecusa(venda_casais(documento=FORA, tabela="cheio", conferido=True),
                          "Não foi possível conferir")

    def test_cnpj_invalido(self):
        self.assertRecusa(venda_casais(documento="22.122.190/0001-53"), "CNPJ do contratante inválido")
        self.assertRecusa(venda_casais(documento=CPF), "CNPJ do contratante inválido")
        self.conecta.assert_not_called()

    def test_le_a_resposta_que_a_tela_ja_pediu(self):
        """A tela perguntou ao abrir o CNPJ; o comprovante não gasta outra pergunta."""
        cliente_logado().post("/api/casais/elite", {"cnpj": ATIVO}, format="json")
        self.assertAssina(venda_casais(documento=ATIVO, tabela="elite", conferido=True))
        self.conecta.assert_called_once()

    @override_settings(LOBBY_CASAIS_CONSULTAS_POR_HORA=1)
    def test_o_comprovante_gasta_o_teto_de_quem_vende(self):
        """Senão um CNPJ novo por comprovante esgotaria o balde do Conecta."""
        self.assertAssina(venda_casais(documento=INATIVO, tabela="cheio", conferido=True))
        resposta = emite_comprovante(
            venda_casais(documento=ATIVO, tabela="elite", conferido=True, protocolo="CAP-261125112346")
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.conecta.call_count, 1)
        # Outro consultor ainda pergunta.
        outro = consultor("outra@sejaap.com.br")
        resposta = emite_comprovante(
            venda_casais(documento=ATIVO, tabela="elite", conferido=True, protocolo="CAP-261125112347"),
            vendedor=outro,
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())


@override_settings(LOBBY_N8N_TOKEN=N8N)
class ValidacaoTest(Base):
    """O "Como confirmo" 6: o `/api/venda/validar` com o que não pode passar."""

    def validar(self, token, valores):
        return APIClient().post(
            "/api/venda/validar",
            {"comprovante": token, "valores": valores},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {N8N}",
        ).json()

    def test_venda_legitima_e_aceita(self):
        for dados in (
            venda_casais(documento=ATIVO, tabela="elite", conferido=True),
            venda_casais(documento=INATIVO, tabela="cheio", conferido=True, protocolo="CAP-261125100002"),
            venda_pf(tabela="elite", protocolo="CAP-261125100003"),
        ):
            with self.subTest(dados=dados["casais"]):
                corpo = self.validar(self.assertAssina(dados), dados)
                self.assertTrue(corpo["valido"], corpo)
                self.assertEqual(corpo["fluxo"], "casais")

    def test_mascara_diferente_no_documento_nao_e_adulteracao(self):
        """A tela manda mascarado; o n8n pode repassar sem máscara."""
        dados = venda_casais(documento=ATIVO, tabela="elite", conferido=True)
        token = self.assertAssina(dados)
        sem_mascara = venda_casais(documento="22122190000152", tabela="elite", conferido=True)
        self.assertTrue(self.validar(token, sem_mascara)["valido"])

    def test_35_mil_numa_pj_que_o_conecta_diz_nao_ser_ativa(self):
        """O servidor não emite (422); e o comprovante do cheio dela não serve para o Elite."""
        self.assertEqual(
            self.emitir(venda_casais(documento=INATIVO, tabela="elite", conferido=True)).status_code, 422
        )
        cheio = venda_casais(documento=INATIVO, tabela="cheio", conferido=True)
        token = self.assertAssina(cheio)
        elite = venda_casais(documento=INATIVO, tabela="elite", conferido=True)
        corpo = self.validar(token, elite)
        self.assertFalse(corpo["valido"])
        self.assertEqual(corpo["motivo"], "valores_adulterados")
        # Só o valor trocado, sem a tabela: valor fora da tabela.
        corpo = self.validar(token, {**cheio, "valor_total": 35000, "entrada": 35000})
        self.assertEqual((corpo["valido"], corpo["motivo"]), (False, "valores_invalidos"))
        # E o comprovante segue de pé para a venda de verdade.
        self.assertTrue(self.validar(token, cheio)["valido"])

    def test_valor_fora_dos_dois(self):
        dados = venda_pf(tabela="cheio")
        token = self.assertAssina(dados)
        corpo = self.validar(token, {**dados, "valor_total": 30000, "entrada": 30000})
        self.assertEqual((corpo["valido"], corpo["motivo"]), (False, "valores_invalidos"))
        self.assertIn("R$ 39.997,00", corpo["detalhe"])

    def test_o_cnpj_e_assinado(self):
        """O comprovante do Elite de um cliente ativo não vende o Elite a outra empresa."""
        token = self.assertAssina(venda_casais(documento=ATIVO, tabela="elite", conferido=True))
        outra = venda_casais(documento=INATIVO, tabela="elite", conferido=True)
        self.assertEqual(self.validar(token, outra)["motivo"], "valores_adulterados")

    def test_comprovante_forjado(self):
        dados = venda_casais(documento=ATIVO, tabela="elite", conferido=True)
        self.assertEqual(self.validar("00000000-0000-0000-0000-000000000000.abc", dados)["motivo"],
                         "comprovante_desconhecido")

    def test_so_o_comprovante_que_passaria_pergunta_ao_conecta(self):
        """O webhook é público: um POST com comprovante falso não gasta o balde do Conecta."""
        dados = venda_casais(documento=ATIVO, tabela="elite", conferido=True)
        token = self.assertAssina(dados)
        identificador = token.split(".")[0]
        outro_cnpj = venda_casais(documento=FORA, tabela="elite", conferido=True)
        for token_da_vez, valores, motivo in (
            (None, dados, "comprovante_ausente"),
            ("00000000-0000-0000-0000-000000000000.abc", dados, "comprovante_desconhecido"),
            ("x.y", dados, "comprovante_desconhecido"),
            (f"{identificador}.assinatura-errada", dados, "assinatura_invalida"),
            (token, outro_cnpj, "valores_adulterados"),
        ):
            with self.subTest(motivo=motivo):
                cache.clear()
                self.conecta.reset_mock()
                corpo = self.validar(token_da_vez, valores)
                self.assertEqual((corpo["valido"], corpo["motivo"]), (False, motivo))
                self.conecta.assert_not_called()

        # Usado: o reenvio do n8n também não pergunta.
        self.assertTrue(self.validar(token, dados)["valido"])
        cache.clear()
        self.conecta.reset_mock()
        self.assertEqual(self.validar(token, outro_cnpj)["motivo"], "comprovante_ja_usado")
        self.conecta.assert_not_called()

    def test_valores_que_nao_sao_objeto_nao_dao_500(self):
        token = self.assertAssina(venda_pf(tabela="cheio"))
        for valores, motivo in (("abc", "comprovante_ausente"), (["x"], "valores_invalidos")):
            with self.subTest(valores=valores):
                corpo = self.validar(None if motivo == "comprovante_ausente" else token, valores)
                self.assertEqual((corpo["valido"], corpo["motivo"]), (False, motivo))

    def test_elite_de_uma_pj_que_deixou_de_ser_ativa_e_recusado(self):
        """O servidor reconsulta o Conecta na validação."""
        dados = venda_casais(documento=ATIVO, tabela="elite", conferido=True)
        token = self.assertAssina(dados)
        cache.clear()
        self.conecta.side_effect = lambda cnpj: False
        corpo = self.validar(token, dados)
        self.assertEqual((corpo["valido"], corpo["motivo"]), (False, "elite_nao_confirmado"))
        self.assertIsNone(ComprovanteVenda.objects.get().consumido_em)

    def test_sem_resposta_do_conecta_na_validacao_vale_a_da_emissao(self):
        """O n8n não bloqueia uma venda conferida porque o Conecta piscou."""
        dados = venda_casais(documento=ATIVO, tabela="elite", conferido=True)
        token = self.assertAssina(dados)
        cache.clear()
        self.conecta.side_effect = SemResposta("indisponivel")
        self.assertTrue(self.validar(token, dados)["valido"])

    def test_a_validacao_le_a_resposta_guardada(self):
        dados = venda_casais(documento=ATIVO, tabela="elite", conferido=True)
        token = self.assertAssina(dados)
        self.assertTrue(self.validar(token, dados)["valido"])
        self.conecta.assert_called_once()

    def test_a_validacao_devolve_o_que_foi_assinado_sobre_quem_contrata(self):
        """O topo do payload é do navegador: o n8n confere o contratante contra isto.

        O caso: um comprovante legítimo da PF a R$ 35.000 (o consultor decide), e
        o topo dizendo que quem contrata é uma PJ. O hash bate — o servidor nunca
        vê o topo —, e é o `casais` devolvido aqui que mostra ao n8n que o
        comprovante é de uma PF.
        """
        dados = venda_pf(tabela="elite")
        corpo = self.validar(self.assertAssina(dados), dados)
        self.assertTrue(corpo["valido"], corpo)
        self.assertEqual(corpo["valor_total"], "35000.00")
        self.assertEqual(corpo["casais"], {
            "tipo_pessoa": "PF", "documento": "", "tabela": "elite", "elite_conferido_no_conecta": False,
            "elite_decidido_por": "consultor",
        })

        pj = venda_casais(documento=ATIVO, tabela="elite", conferido=True, protocolo="CAP-261125100009")
        corpo = self.validar(self.assertAssina(pj), pj)
        self.assertEqual(corpo["casais"]["documento"], "22122190000152")
        self.assertEqual(corpo["casais"]["elite_decidido_por"], "conecta")

    def test_marcado_a_mao_com_o_conecta_ainda_fora_passa(self):
        """O "Como confirmo" 3 da TSK-1640: a validação reconsulta, e segue sem resposta."""
        dados = venda_casais(documento=FORA, tabela="elite", conferido=False)
        token = self.assertAssina(dados)
        # Sem a pausa da emissão: a validação pergunta de novo, de fato.
        cache.clear()
        self.conecta.reset_mock()
        corpo = self.validar(token, dados)
        self.assertTrue(corpo["valido"], corpo)
        self.conecta.assert_called_once_with("63580153000175")
        self.assertEqual(corpo["valor_total"], "35000.00")
        self.assertEqual(corpo["casais"], {
            "tipo_pessoa": "PJ", "documento": "63580153000175", "tabela": "elite",
            "elite_conferido_no_conecta": False, "elite_decidido_por": "consultor_sem_conecta",
        })

    def test_marcado_a_mao_e_o_conecta_volta_dizendo_que_nao_e_recusado(self):
        """O "Como confirmo" 4 da TSK-1640: a reconsulta do servidor diz que não é ativa."""
        dados = venda_casais(documento=FORA, tabela="elite", conferido=False)
        token = self.assertAssina(dados)
        cache.clear()
        self.conecta.side_effect = lambda cnpj: False
        corpo = self.validar(token, dados)
        self.assertEqual((corpo["valido"], corpo["motivo"]), (False, "elite_nao_confirmado"))
        self.assertIsNone(ComprovanteVenda.objects.get().consumido_em)

    def test_marcado_a_mao_e_o_conecta_volta_dizendo_que_sim(self):
        dados = venda_casais(documento=FORA, tabela="elite", conferido=False)
        token = self.assertAssina(dados)
        cache.clear()
        self.conecta.side_effect = lambda cnpj: True
        corpo = self.validar(token, dados)
        self.assertTrue(corpo["valido"], corpo)
        # Quem decidiu foi o consultor: é o que foi assinado.
        self.assertEqual(corpo["casais"]["elite_decidido_por"], "consultor_sem_conecta")

    def test_o_cheio_marcado_a_mao_nao_reconsulta(self):
        dados = venda_casais(documento=FORA, tabela="cheio", conferido=False)
        token = self.assertAssina(dados)
        cache.clear()
        self.conecta.reset_mock()
        corpo = self.validar(token, dados)
        self.assertTrue(corpo["valido"], corpo)
        self.assertEqual(corpo["casais"]["elite_decidido_por"], "consultor_sem_conecta")
        self.conecta.assert_not_called()

    def test_o_elite_decidido_por_nao_entra_no_que_e_assinado(self):
        """Sai do assinado; se vier nos valores, é ignorado, e o hash é o de antes da TSK-1640.

        Assim um comprovante emitido antes do deploy continua valendo depois dele.
        """
        dados = venda_casais(documento=ATIVO, tabela="elite", conferido=True)
        token = self.assertAssina(dados)
        self.assertEqual(set(ComprovanteVenda.objects.get().valores["casais"]),
                         {"tipo_pessoa", "documento", "tabela", "elite_conferido_no_conecta"})
        com_a_chave = {**dados, "casais": {**dados["casais"], "elite_decidido_por": "consultor_sem_conecta"}}
        corpo = self.validar(token, com_a_chave)
        self.assertTrue(corpo["valido"], corpo)
        self.assertEqual(corpo["casais"]["elite_decidido_por"], "conecta")

    def test_os_outros_fluxos_nao_ganham_o_bloco(self):
        dados = venda_de_tabela()
        token = emite_comprovante(dados).json()["comprovante"]
        corpo = self.validar(token, dados)
        self.assertTrue(corpo["valido"], corpo)
        self.assertNotIn("casais", corpo)
        self.assertNotIn("valor_total", corpo)

    def test_cheio_nao_reconsulta_na_validacao(self):
        dados = venda_casais(documento=INATIVO, tabela="cheio", conferido=True)
        token = self.assertAssina(dados)
        cache.clear()
        self.conecta.reset_mock()
        self.assertTrue(self.validar(token, dados)["valido"])
        self.conecta.assert_not_called()


class OutrosFluxosNaoMudamTest(TestCase):
    def test_o_bloco_do_casais_nao_entra_no_que_o_elite_assina(self):
        resposta = emite_comprovante(venda_de_tabela(casais={"tabela": "elite"}))
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("casais", ComprovanteVenda.objects.get().valores)
