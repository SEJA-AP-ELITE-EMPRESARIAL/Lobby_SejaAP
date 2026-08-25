"""
Criar e editar produto pela tela da diretoria.

Isto sempre foi possível no /django-admin/. O que muda aqui é que passa a deixar
rastro: toda criação e toda edição abrem vigência na linha do tempo, com autor.
Era esse o buraco — o caminho de menor esforço para mexer no catálogo era
justamente o que não registrava nada.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.contas.models import Papel

from .models import Produto, Vigencia
from .tests_escrita import cria_pessoa


NOVO_RECORRENTE = {
    "categoria_id": "elite",
    "nome": "ELITE MASTER",
    "sigla": "MAS",
    "descricao": "O plano do Domínio",
    "duracao": "12 meses",
    "icone": "shield",
    "recorrente": True,
    "mensalidade": "39997",
    "vigencia_meses": 12,
}


class BaseProdutos(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)

    def como_diretoria(self):
        cliente = APIClient()
        cliente.force_authenticate(user=self.diretoria)
        return cliente

    def cria(self, **campos):
        return self.como_diretoria().post(
            "/api/produtos", {**NOVO_RECORRENTE, **campos}, format="json"
        )


class PermissaoTest(BaseProdutos):
    def test_anonimo_nao_cria(self):
        resposta = self.client.post("/api/produtos", NOVO_RECORRENTE, format="json")
        self.assertEqual(resposta.status_code, 401)
        self.assertIn("erro", resposta.json())

    def test_gerente_nao_cria(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        cliente = APIClient()
        cliente.force_authenticate(user=gerente)
        resposta = cliente.post("/api/produtos", NOVO_RECORRENTE, format="json")
        self.assertEqual(resposta.status_code, 403)

    def test_nao_existe_apagar_produto(self):
        """Produto vendido aparece no protocolo de vendas fechadas. Apagar deixa
        esses protocolos órfãos — a mesma razão do PROTECT no modelo."""
        self.cria()
        resposta = self.como_diretoria().delete("/api/produtos/elite/elite-master")
        self.assertEqual(resposta.status_code, 405)
        self.assertTrue(Produto.objects.filter(slug="elite-master").exists())


class CriacaoTest(BaseProdutos):
    def test_cria_plano_recorrente(self):
        resposta = self.cria()
        self.assertEqual(resposta.status_code, 201, resposta.json())

        produto = resposta.json()["produto"]
        self.assertEqual(produto["name"], "ELITE MASTER")
        self.assertEqual(produto["sigla"], "MAS")
        self.assertEqual(produto["monthly"], 39997)
        self.assertEqual(produto["price"], 39997 * 12)
        self.assertEqual(produto["vigencia"], 12)

    def test_aparece_no_catalogo_publico_na_hora(self):
        self.cria()
        cats = self.client.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        self.assertIn("elite-master", [p["id"] for p in elite["products"]])

    def test_slug_sai_do_nome_quando_nao_vem(self):
        """Id com espaço ou acento iria no payload da venda e no `find` do front."""
        self.cria(nome="ELITE PREPARAÇÃO PLUS", sigla="PLU")
        self.assertTrue(Produto.objects.filter(slug="elite-preparacao-plus").exists())

    def test_slug_informado_e_respeitado(self):
        self.cria(slug="master")
        self.assertTrue(Produto.objects.filter(slug="master").exists())

    def test_entra_no_fim_da_lista_quando_a_ordem_nao_vem(self):
        self.cria()
        produto = Produto.objects.get(slug="elite-master")
        # Os seis planos da Elite ocupam 0..5 (a escada de preço).
        self.assertEqual(produto.ordem, 6)

    def test_cria_produto_avulso(self):
        resposta = self.cria(
            nome="ELITE INTENSIVO",
            sigla="INT",
            recorrente=False,
            valor="28000",
            mensalidade=None,
            vigencia_meses=None,
        )
        self.assertEqual(resposta.status_code, 201, resposta.json())
        produto = resposta.json()["produto"]
        self.assertEqual(produto["price"], 28000)
        for campo in ("monthly", "recurring", "vigencia"):
            self.assertNotIn(campo, produto)

    def test_abre_a_vigencia_do_valor_com_autor(self):
        self.cria()
        registro = Vigencia.objects.get(
            chave="elite-master", campo=Vigencia.Campo.MENSALIDADE
        )
        self.assertEqual(registro.valor, "39997.00")
        self.assertIsNone(registro.vigente_ate)
        self.assertEqual(registro.autor_email, "diretoria@sejaap.com.br")

    def test_sigla_repetida_volta_como_mensagem_e_nao_como_500(self):
        resposta = self.cria(sigla="PRO")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("ELITE PRO", resposta.json()["erro"])

    def test_sigla_da_apn_e_recusada(self):
        resposta = self.cria(sigla="APN")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("APN", resposta.json()["erro"])

    def test_sigla_minuscula_e_normalizada(self):
        self.cria(sigla="mas")
        self.assertEqual(Produto.objects.get(slug="elite-master").sigla, "MAS")

    def test_slug_repetido_na_categoria_e_recusado(self):
        resposta = self.cria(slug="pro", sigla="NOV")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Já existe", resposta.json()["erro"])

    def test_categoria_inexistente_e_recusada(self):
        resposta = self.cria(categoria_id="fantasma")
        self.assertEqual(resposta.status_code, 400)

    def test_categoria_de_fluxo_proprio_nao_recebe_produto(self):
        """A APN não tem tabela de preços — não há produto a criar nela."""
        resposta = self.cria(categoria_id="apn", sigla="NOV")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("fluxo próprio", resposta.json()["erro"])

    def test_mensalidade_invalida_e_recusada(self):
        for valor in ("0", "-10", "abacaxi", None):
            with self.subTest(valor=valor):
                resposta = self.cria(mensalidade=valor)
                self.assertEqual(resposta.status_code, 400)

    def test_vigencia_fora_da_faixa_e_recusada(self):
        resposta = self.cria(vigencia_meses=999)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Vigência", resposta.json()["erro"])

    def test_sem_nome_e_recusado(self):
        resposta = self.cria(nome="  ")
        self.assertEqual(resposta.status_code, 400)


class EdicaoTest(BaseProdutos):
    def test_edita_nome_e_descricao(self):
        resposta = self.como_diretoria().patch(
            "/api/produtos/elite/pro",
            {"nome": "ELITE PRO 2027", "descricao": "O plano da Estrutura, revisado"},
            format="json",
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())
        produto = Produto.objects.get(slug="pro")
        self.assertEqual(produto.nome, "ELITE PRO 2027")
        # O que não veio no corpo não muda.
        self.assertEqual(produto.mensalidade, Decimal("12997.00"))
        self.assertEqual(produto.sigla, "PRO")

    def test_editar_valor_abre_vigencia_nova(self):
        self.como_diretoria().patch(
            "/api/produtos/elite/pro",
            {"recorrente": True, "mensalidade": "13500", "vigencia_meses": 12},
            format="json",
        )
        registros = list(
            Vigencia.objects.filter(chave="pro", campo=Vigencia.Campo.MENSALIDADE)
        )
        self.assertEqual(len(registros), 2)
        self.assertEqual(registros[0].valor, "13500.00")
        self.assertIsNotNone(registros[1].vigente_ate)

    def test_editar_a_vigencia_muda_o_total_do_contrato(self):
        """`price` é derivado: mudar a vigência tem que mover o total junto."""
        resposta = self.como_diretoria().patch(
            "/api/produtos/elite/pro",
            {"recorrente": True, "mensalidade": "12997", "vigencia_meses": 24},
            format="json",
        )
        self.assertEqual(resposta.json()["produto"]["price"], 12997 * 24)

    def test_trocar_recorrente_por_avulso_limpa_a_outra_metade(self):
        """As CheckConstraint exigem que só uma das duas exista. Sem a limpeza, a
        gravação morreria num erro de constraint que não diz nada a quem edita."""
        resposta = self.como_diretoria().patch(
            "/api/produtos/elite/pro",
            {"recorrente": False, "valor": "50000"},
            format="json",
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())
        produto = Produto.objects.get(slug="pro")
        self.assertIsNone(produto.mensalidade)
        self.assertIsNone(produto.vigencia_meses)
        self.assertEqual(produto.valor, Decimal("50000.00"))

    def test_edita_texto_de_produto_de_formulario_sem_criar_preco(self):
        """A tela manda o formulário inteiro, inclusive os campos de preço.

        O Recrutamento e Seleção não tem preço nenhum, e não pode ganhar um por
        efeito colateral de alguém corrigir a descrição dele. Os campos de valor
        que vierem no corpo são descartados; o resto grava normalmente.
        """
        resposta = self.como_diretoria().patch(
            "/api/produtos/produtos/recrutamento-selecao",
            {
                "nome": "Recrutamento e Seleção",
                "descricao": "Processo seletivo conduzido pela Seja AP",
                "recorrente": False,
                "valor": "9900",
            },
            format="json",
        )

        self.assertEqual(resposta.status_code, 200, resposta.json())
        produto = Produto.objects.get(slug="recrutamento-selecao")
        self.assertEqual(produto.descricao, "Processo seletivo conduzido pela Seja AP")
        self.assertEqual(produto.fluxo, "dh")
        self.assertIsNone(produto.valor)
        self.assertIsNone(produto.mensalidade)

    def test_produto_inexistente_pede_recarregar(self):
        resposta = self.como_diretoria().patch(
            "/api/produtos/elite/fantasma", {"nome": "X"}, format="json"
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Recarregue", resposta.json()["erro"])

    def test_sigla_repetida_na_edicao_e_recusada(self):
        resposta = self.como_diretoria().patch(
            "/api/produtos/elite/pro", {"sigla": "EVO"}, format="json"
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Produto.objects.get(slug="pro").sigla, "PRO")

    def test_manter_a_propria_sigla_nao_e_conflito(self):
        """O produto não pode colidir consigo mesmo — erro clássico de validação
        de unicidade em edição."""
        resposta = self.como_diretoria().patch(
            "/api/produtos/elite/pro", {"sigla": "PRO", "nome": "ELITE PRO"}, format="json"
        )
        self.assertEqual(resposta.status_code, 200, resposta.json())

    def test_gerente_nao_edita(self):
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        cliente = APIClient()
        cliente.force_authenticate(user=gerente)
        resposta = cliente.patch("/api/produtos/elite/pro", {"nome": "X"}, format="json")
        self.assertEqual(resposta.status_code, 403)
