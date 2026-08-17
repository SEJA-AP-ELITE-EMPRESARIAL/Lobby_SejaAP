"""
A política de cobrança e a linha do tempo.

O que estes testes protegem, em uma frase: **a data com que o cronograma nasce
saiu do código e virou dado**, e mexer nela deixa rastro de quem, quando e por
quanto tempo valeu.

Duas invariantes aparecem repetidas aqui porque são as que, se quebrarem, não
dão erro em lugar nenhum — só uma resposta errada meses depois:

1. Existe UMA política geral. Duas, e metade dos produtos cobraria num dia.
2. Existe UM valor aberto por campo na linha do tempo. Dois, e "quanto custava
   em março" passa a ter duas respostas.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.contas.models import Papel

from . import vigencias
from .models import (
    Categoria,
    PoliticaCobranca,
    PrimeiroVencimento,
    Produto,
    Vigencia,
)
from .tests_escrita import cria_pessoa


REGRA_PADRAO = {
    "dia_vencimento": 15,
    "primeiro_vencimento": PrimeiroVencimento.MES_SEGUINTE,
    "entrada_prazo_dias": 0,
}


class BaseCobranca(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)

    def como_diretoria(self):
        cliente = APIClient()
        cliente.force_authenticate(user=self.diretoria)
        return cliente

    def publica(self, corpo):
        return self.como_diretoria().put("/api/cobranca", corpo, format="json")


class LeituraTest(BaseCobranca):
    def test_get_e_anonimo(self):
        """O consultor não faz login — sem a política ele não monta cronograma."""
        resposta = APIClient().get("/api/cobranca")
        self.assertEqual(resposta.status_code, 200)

    def test_traz_a_politica_geral_com_os_padroes_da_semente(self):
        corpo = self.client.get("/api/cobranca").json()
        self.assertEqual(corpo["geral"], REGRA_PADRAO)
        self.assertEqual(corpo["excecoes"], [])

    def test_traz_as_opcoes_validas_de_cada_campo(self):
        """A tela monta o `select` e os limites a partir daqui, em vez de repetir
        as regras do backend em JavaScript — que é como as duas versões divergem."""
        opcoes = self.client.get("/api/cobranca").json()["opcoes"]
        self.assertEqual(
            [o["valor"] for o in opcoes["primeiro_vencimento"]],
            ["mes_seguinte", "proximo"],
        )
        self.assertEqual(opcoes["dia_vencimento"], {"min": 1, "max": 28})
        self.assertEqual(opcoes["entrada_prazo_dias"], {"min": 0, "max": 90})

    def test_catalogo_carrega_a_politica_no_mesmo_envelope(self):
        """Uma chamada só: o front precisa de preço e data na mesma abertura."""
        corpo = self.client.get("/api/catalogo").json()
        self.assertEqual(corpo["cobranca"], REGRA_PADRAO)

    def test_produto_sem_excecao_nao_emite_cobranca(self):
        """Contrato preservado: produto que herda fica idêntico ao de hoje."""
        cats = self.client.get("/api/catalogo").json()["cats"]
        for categoria in cats:
            for produto in categoria["products"]:
                self.assertNotIn("cobranca", produto)

    def test_produto_com_excecao_emite_cobranca(self):
        self.publica(
            {
                "geral": REGRA_PADRAO,
                "excecoes": [
                    {
                        "categoria_id": "elite",
                        "produto_id": "evo",
                        "dia_vencimento": 5,
                        "primeiro_vencimento": "proximo",
                        "entrada_prazo_dias": 3,
                    }
                ],
            }
        )
        cats = self.client.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        evo = next(p for p in elite["products"] if p["id"] == "evo")
        pro = next(p for p in elite["products"] if p["id"] == "pro")

        self.assertEqual(
            evo["cobranca"],
            {"dia_vencimento": 5, "primeiro_vencimento": "proximo", "entrada_prazo_dias": 3},
        )
        self.assertNotIn("cobranca", pro)

    def test_resposta_nunca_e_cacheavel(self):
        """Atrás da Cloudflare, data de cobrança cacheada é venda com o cronograma
        errado — o mesmo motivo do catálogo."""
        self.assertEqual(self.client.get("/api/cobranca")["Cache-Control"], "no-store")


class PermissaoTest(BaseCobranca):
    def test_anonimo_nao_publica(self):
        resposta = self.client.put(
            "/api/cobranca", {"geral": REGRA_PADRAO}, format="json"
        )
        self.assertEqual(resposta.status_code, 401)

    def test_gerente_nao_publica(self):
        """Gerente autoriza exceção numa venda; a política vale para todas."""
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        cliente = APIClient()
        cliente.force_authenticate(user=gerente)
        resposta = cliente.put("/api/cobranca", {"geral": REGRA_PADRAO}, format="json")
        self.assertEqual(resposta.status_code, 403)
        self.assertIn("erro", resposta.json())

    def test_diretoria_publica(self):
        resposta = self.publica({"geral": {**REGRA_PADRAO, "dia_vencimento": 10}})
        self.assertEqual(resposta.status_code, 200, resposta.json())
        self.assertEqual(PoliticaCobranca.geral_atual().dia_vencimento, 10)


class ValidacaoTest(BaseCobranca):
    def test_dia_fora_da_faixa_e_recusado(self):
        for dia in (0, 29, 31, 99):
            with self.subTest(dia=dia):
                resposta = self.publica({"geral": {**REGRA_PADRAO, "dia_vencimento": dia}})
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("1 a 28", resposta.json()["erro"])

    def test_dia_29_a_31_e_recusado_porque_nao_existe_em_todo_mes(self):
        """O ponto de fixar o dia é ter UMA data de cobrança em lote. Aceitar 31
        traria de volta o "cair no último dia" que a mudança eliminou."""
        self.assertEqual(self.publica({"geral": {**REGRA_PADRAO, "dia_vencimento": 31}}).status_code, 400)
        self.assertEqual(PoliticaCobranca.geral_atual().dia_vencimento, 15)

    def test_regra_de_primeira_parcela_desconhecida_e_recusada(self):
        resposta = self.publica(
            {"geral": {**REGRA_PADRAO, "primeiro_vencimento": "quando_der"}}
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("desconhecida", resposta.json()["erro"])

    def test_prazo_da_entrada_acima_do_teto_e_recusado(self):
        resposta = self.publica({"geral": {**REGRA_PADRAO, "entrada_prazo_dias": 120}})
        self.assertEqual(resposta.status_code, 400)

    def test_excecao_de_produto_inexistente_e_recusada(self):
        resposta = self.publica(
            {
                "geral": REGRA_PADRAO,
                "excecoes": [{"categoria_id": "elite", "produto_id": "fantasma", **REGRA_PADRAO}],
            }
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Recarregue", resposta.json()["erro"])

    def test_excecao_repetida_e_recusada(self):
        resposta = self.publica(
            {
                "geral": REGRA_PADRAO,
                "excecoes": [
                    {"categoria_id": "elite", "produto_id": "pro", **REGRA_PADRAO},
                    {"categoria_id": "elite", "produto_id": "pro", **REGRA_PADRAO},
                ],
            }
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("repetido", resposta.json()["erro"])

    def test_recusa_nao_deixa_publicacao_pela_metade(self):
        """A exceção inválida vem DEPOIS da geral no corpo: se a validação não
        acontecesse toda antes da escrita, a geral já teria mudado."""
        resposta = self.publica(
            {
                "geral": {**REGRA_PADRAO, "dia_vencimento": 10},
                "excecoes": [{"categoria_id": "elite", "produto_id": "pro", **REGRA_PADRAO, "dia_vencimento": 99}],
            }
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(PoliticaCobranca.geral_atual().dia_vencimento, 15)
        self.assertFalse(PoliticaCobranca.objects.filter(geral=False).exists())


class ExcecaoPorProdutoTest(BaseCobranca):
    def _excecao(self, **campos):
        return {
            "categoria_id": "elite",
            "produto_id": "evo",
            **REGRA_PADRAO,
            **campos,
        }

    def test_cria_e_depois_remove_a_excecao(self):
        self.publica({"geral": REGRA_PADRAO, "excecoes": [self._excecao(dia_vencimento=5)]})
        self.assertEqual(PoliticaCobranca.objects.filter(geral=False).count(), 1)

        # A tela reenvia a lista completa; a exceção que não vem, sai.
        self.publica({"geral": REGRA_PADRAO, "excecoes": []})
        self.assertEqual(PoliticaCobranca.objects.filter(geral=False).count(), 0)

    def test_remover_a_excecao_fecha_a_vigencia_dela(self):
        """Sem isto, a trilha diria que o produto cobra no dia 5 até hoje — e o
        consultor estaria vendo dia 15 na tela."""
        self.publica({"geral": REGRA_PADRAO, "excecoes": [self._excecao(dia_vencimento=5)]})
        self.publica({"geral": REGRA_PADRAO, "excecoes": []})

        abertas = Vigencia.objects.filter(
            chave="evo", campo=Vigencia.Campo.DIA_VENCIMENTO, vigente_ate__isnull=True
        )
        self.assertFalse(abertas.exists())
        fechada = Vigencia.objects.get(chave="evo", campo=Vigencia.Campo.DIA_VENCIMENTO)
        self.assertEqual(fechada.valor, "5")
        self.assertIsNotNone(fechada.vigente_ate)

    def test_remover_a_excecao_nao_toca_no_preco_do_produto(self):
        """`encerrar` fecha datas, não dinheiro: o preço do EVO não deixou de valer."""
        self.publica({"geral": REGRA_PADRAO, "excecoes": [self._excecao(dia_vencimento=5)]})
        self.publica({"geral": REGRA_PADRAO, "excecoes": []})
        self.assertTrue(
            Vigencia.objects.filter(
                chave="evo", campo=Vigencia.Campo.MENSALIDADE, vigente_ate__isnull=True
            ).exists()
        )

    def test_uma_unica_politica_geral(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PoliticaCobranca.objects.create(geral=True, produto=None)

    def test_politica_sem_produto_e_sem_geral_e_recusada(self):
        """As duas metades da mesma invariante: geral não tem produto, exceção tem."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PoliticaCobranca.objects.create(geral=False, produto=None)


class LinhaDoTempoTest(BaseCobranca):
    def test_a_semente_abre_a_vigencia_do_que_ja_existia(self):
        """Sem isto o preço em vigor apareceria na tela sem origem."""
        pro = Vigencia.objects.get(
            chave="pro", campo=Vigencia.Campo.MENSALIDADE, vigente_ate__isnull=True
        )
        self.assertEqual(pro.valor, "12997.00")
        self.assertIsNone(pro.autor)

    def test_publicar_preco_fecha_a_vigencia_antiga_e_abre_a_nova(self):
        cats = self.client.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        next(p for p in elite["products"] if p["id"] == "pro")["monthly"] = 14000
        self.como_diretoria().put("/api/catalogo", {"cats": cats}, format="json")

        registros = list(
            Vigencia.objects.filter(chave="pro", campo=Vigencia.Campo.MENSALIDADE)
        )
        self.assertEqual(len(registros), 2)
        atual, anterior = registros  # ordering: -vigente_de
        self.assertEqual(atual.valor, "14000.00")
        self.assertIsNone(atual.vigente_ate)
        self.assertEqual(anterior.valor, "12997.00")
        # O fechamento e a abertura no MESMO instante: sem isso existe uma janela
        # sem valor nenhum, e `vigente_em` cairia no vazio.
        self.assertEqual(anterior.vigente_ate, atual.vigente_de)
        self.assertEqual(atual.autor_email, "diretoria@sejaap.com.br")

    def test_publicar_o_mesmo_valor_nao_picota_a_linha_do_tempo(self):
        antes = Vigencia.objects.filter(chave="pro").count()
        cats = self.client.get("/api/catalogo").json()["cats"]
        self.como_diretoria().put("/api/catalogo", {"cats": cats}, format="json")
        self.assertEqual(Vigencia.objects.filter(chave="pro").count(), antes)

    def test_um_unico_valor_aberto_por_campo(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Vigencia.objects.create(
                    chave="pro",
                    rotulo="ELITE PRO",
                    campo=Vigencia.Campo.MENSALIDADE,
                    valor="1.00",
                    vigente_de=timezone.now(),
                )

    def test_vigente_em_responde_o_valor_da_epoca(self):
        """A pergunta que motivou a tabela: quanto custava em março?"""
        corte = timezone.now()
        cats = self.client.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        next(p for p in elite["products"] if p["id"] == "pro")["monthly"] = 14000
        self.como_diretoria().put("/api/catalogo", {"cats": cats}, format="json")

        antes = vigencias.vigente_em("pro", Vigencia.Campo.MENSALIDADE, corte)
        agora = vigencias.vigente_em(
            "pro", Vigencia.Campo.MENSALIDADE, timezone.now() + timedelta(seconds=1)
        )
        self.assertEqual(antes.valor, "12997.00")
        self.assertEqual(agora.valor, "14000.00")

    def test_o_rotulo_e_fotografia_e_nao_referencia(self):
        """Renomear o produto não pode reescrever o passado — a trilha existe
        justamente para contar como era."""
        self.publica({"geral": {**REGRA_PADRAO, "dia_vencimento": 10}})
        produto = Produto.objects.get(slug="pro")
        produto.nome = "ELITE PRO 2027"
        produto.save()

        antigo = Vigencia.objects.filter(chave="pro").order_by("vigente_de").first()
        self.assertEqual(antigo.rotulo, "ELITE PRO")

    def test_publicar_politica_registra_o_autor(self):
        self.publica({"geral": {**REGRA_PADRAO, "dia_vencimento": 10}})
        registro = Vigencia.objects.get(
            chave="geral", campo=Vigencia.Campo.DIA_VENCIMENTO, vigente_ate__isnull=True
        )
        self.assertEqual(registro.valor, "10")
        self.assertEqual(registro.autor_email, "diretoria@sejaap.com.br")


class ValorDeReferenciaDaApnTest(BaseCobranca):
    """A APN deixa de ser somente leitura na tela da diretoria.

    O que NÃO muda: no lobby o valor sempre foi livre, e continua. O valor de
    referência é só o número com que a tela do consultor abre — a APN é venda de
    valor negociado, decisão de 31/07/2026.
    """

    def _publica_referencia(self, valor):
        cats = self.client.get("/api/catalogo").json()["cats"]
        next(c for c in cats if c["id"] == "apn")["valor_referencia"] = valor
        return self.como_diretoria().put("/api/catalogo", {"cats": cats}, format="json")

    def test_apn_nasce_sem_referencia(self):
        """Sem configurar, o campo nem aparece no JSON — contrato de antes."""
        cats = self.client.get("/api/catalogo").json()["cats"]
        apn = next(c for c in cats if c["id"] == "apn")
        self.assertNotIn("valor_referencia", apn)

    def test_diretoria_configura_a_referencia(self):
        resposta = self._publica_referencia(35000)
        self.assertEqual(resposta.status_code, 200, resposta.json())

        apn = next(c for c in resposta.json()["cats"] if c["id"] == "apn")
        self.assertEqual(apn["valor_referencia"], 35000)
        self.assertEqual(Categoria.objects.get(slug="apn").valor_referencia, Decimal("35000.00"))

    def test_a_alteracao_entra_no_resumo_da_publicacao(self):
        resposta = self._publica_referencia(35000)
        self.assertEqual(resposta.json()["alteracoes"], ["APN (valor de referência): None → 35000.00"])

    def test_abre_vigencia_com_autor(self):
        self._publica_referencia(35000)
        registro = Vigencia.objects.get(
            chave="apn", campo=Vigencia.Campo.VALOR_REFERENCIA, vigente_ate__isnull=True
        )
        self.assertEqual(registro.valor, "35000.00")
        self.assertEqual(registro.rotulo, "APN")
        self.assertEqual(registro.autor_email, "diretoria@sejaap.com.br")

    def test_pode_ser_limpa_depois_de_configurada(self):
        """Sem isto não haveria como voltar atrás: a tela abriria sempre com um
        valor que alguém digitou uma vez."""
        self._publica_referencia(35000)
        resposta = self._publica_referencia(None)

        self.assertEqual(resposta.status_code, 200, resposta.json())
        apn = next(c for c in resposta.json()["cats"] if c["id"] == "apn")
        self.assertNotIn("valor_referencia", apn)
        self.assertIsNone(Categoria.objects.get(slug="apn").valor_referencia)

    def test_campo_ausente_no_corpo_nao_mexe_no_que_esta_gravado(self):
        """"Não mandei" é diferente de "mandei vazio" — e só o segundo limpa."""
        self._publica_referencia(35000)
        cats = self.client.get("/api/catalogo").json()["cats"]
        del next(c for c in cats if c["id"] == "apn")["valor_referencia"]
        self.como_diretoria().put("/api/catalogo", {"cats": cats}, format="json")

        self.assertEqual(Categoria.objects.get(slug="apn").valor_referencia, Decimal("35000.00"))

    def test_valor_negativo_e_recusado(self):
        resposta = self._publica_referencia(-1)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Valor de referência inválido", resposta.json()["erro"])

    def test_gerente_nao_configura(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        cats = self.client.get("/api/catalogo").json()["cats"]
        next(c for c in cats if c["id"] == "apn")["valor_referencia"] = 35000
        cliente = APIClient()
        cliente.force_authenticate(user=gerente)

        self.assertEqual(cliente.put("/api/catalogo", {"cats": cats}, format="json").status_code, 403)

    def test_categoria_comum_nao_aceita_valor_de_referencia(self):
        """Numa categoria com tabela de preços, seria um número que ninguém lê —
        e quem o lesse por engano estaria cotando errado."""
        elite = Categoria.objects.get(slug="elite")
        elite.valor_referencia = Decimal("1000")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                elite.save()


class HistoricoApiTest(BaseCobranca):
    def test_anonimo_nao_le_o_historico(self):
        """Não é senha, mas é a série histórica de preço da empresa."""
        self.assertEqual(self.client.get("/api/historico").status_code, 401)

    def test_gerente_nao_le_o_historico(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        cliente = APIClient()
        cliente.force_authenticate(user=gerente)
        self.assertEqual(cliente.get("/api/historico").status_code, 403)

    def test_diretoria_ve_a_linha_do_tempo_com_rotulo_legivel(self):
        resposta = self.como_diretoria().get("/api/historico")
        self.assertEqual(resposta.status_code, 200)
        registros = resposta.json()["registros"]
        self.assertTrue(registros)

        pro = next(r for r in registros if r["chave"] == "pro" and r["campo"] == "mensalidade")
        self.assertEqual(pro["campo_rotulo"], "Mensalidade")
        self.assertTrue(pro["vigente"])
        self.assertIsNone(pro["vigente_ate"])
        self.assertIsNone(pro["autor"])  # semente: não houve pessoa

    def test_filtra_por_chave_e_por_campo(self):
        self.publica({"geral": {**REGRA_PADRAO, "dia_vencimento": 10}})
        cliente = self.como_diretoria()

        so_geral = cliente.get("/api/historico?chave=geral").json()["registros"]
        self.assertTrue(so_geral)
        self.assertTrue(all(r["chave"] == "geral" for r in so_geral))

        so_dia = cliente.get("/api/historico?chave=geral&campo=dia_vencimento").json()["registros"]
        self.assertEqual([r["valor"] for r in so_dia], ["10", "15"])
