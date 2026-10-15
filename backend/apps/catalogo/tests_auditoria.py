"""
Toda mudança do catálogo deixa registro com autor (TSK-1059, ADR-002 §13.3).

Os buracos que estes testes fecham, todos de antes desta tarefa:

1. O `PATCH /api/produtos/<cat>/<slug>` mudava preço sem `PublicacaoCatalogo`.
2. A mudança só de texto (nome, sigla, descrição, duração, ícone) não deixava
   rastro nenhum.
3. Remover uma exceção de cobrança fechava a vigência sem gravar quem removeu:
   `vigencias.encerrar()` recebia o autor e o ignorava.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.contas.models import Papel

from . import vigencias
from .models import PoliticaCobranca, Produto, PublicacaoCatalogo, Vigencia
from .registro import reais
from .tests_escrita import cria_pessoa


class Base(TestCase):
    def setUp(self):
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.diretoria)

    def ultima(self):
        return PublicacaoCatalogo.objects.order_by("-publicado_em", "-id").first()


class PatchDeProdutoTest(Base):
    def test_mudanca_de_preco_vira_publicacao_com_autor(self):
        antes = PublicacaoCatalogo.objects.count()
        resposta = self.cliente.patch(
            "/api/produtos/elite/pro",
            {"recorrente": True, "mensalidade": "10497", "vigencia_meses": 12},
            format="json",
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(PublicacaoCatalogo.objects.count(), antes + 1)
        publicacao = self.ultima()
        self.assertEqual(publicacao.autor, self.diretoria)
        self.assertEqual(publicacao.autor_email, "diretoria@sejaap.com.br")
        self.assertIn("ELITE PRO: mensalidade R$ ", publicacao.resumo)
        self.assertIn("→ R$ 10.497,00", publicacao.resumo)
        self.assertEqual(publicacao.alvos, "|elite/pro|")

    def test_mudanca_so_de_texto_tambem_fica_registrada(self):
        resposta = self.cliente.patch(
            "/api/produtos/elite/pro",
            {"nome": "ELITE PRO+", "descricao": "Nova descrição", "icone": "star"},
            format="json",
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        linhas = self.ultima().resumo.splitlines()
        self.assertIn('ELITE PRO: nome "ELITE PRO" → "ELITE PRO+"', linhas)
        self.assertIn("ELITE PRO: ícone \"workspace_premium\" → \"star\"", linhas)
        self.assertTrue(any(linha.startswith("ELITE PRO: descrição ") for linha in linhas))

    def test_a_publicacao_traz_o_catalogo_como_ficou(self):
        self.cliente.patch("/api/produtos/elite/pro", {"nome": "ELITE PRO+"}, format="json")
        elite = next(c for c in self.ultima().catalogo if c["id"] == "elite")
        self.assertIn("ELITE PRO+", [p["name"] for p in elite["products"]])


class CriacaoDeProdutoTest(Base):
    def test_produto_novo_vira_publicacao(self):
        resposta = self.cliente.post(
            "/api/produtos",
            {"categoria_id": "elite", "nome": "ELITE MASTER", "sigla": "MAS", "recorrente": True,
             "mensalidade": "39997", "vigencia_meses": 12},
            format="json",
        )
        self.assertEqual(resposta.status_code, 201, resposta.content)
        publicacao = self.ultima()
        self.assertEqual(publicacao.autor, self.diretoria)
        self.assertEqual(
            publicacao.resumo, "Novo produto em Elite: ELITE MASTER (MAS), R$ 39.997,00 × 12 meses"
        )
        self.assertEqual(publicacao.alvos, "|elite/elite-master|")


class PutDoCatalogoTest(Base):
    def test_publicacao_guarda_os_alvos_para_o_filtro_por_produto(self):
        cats = self.cliente.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        pro = next(p for p in elite["products"] if p["id"] == "pro")
        pro["monthly"] = pro["monthly"] + 100
        resposta = self.cliente.put("/api/catalogo", {"cats": cats}, format="json")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.ultima().alvos, "|elite/pro|")
        # O formato do resumo do PUT não muda: o /admin de hoje o lê assim.
        self.assertRegex(self.ultima().resumo, r"^ELITE PRO: \d+\.\d\d → \d+\.\d\d$")


class RemocaoDeExcecaoTest(Base):
    def test_remover_a_excecao_grava_quem_removeu(self):
        regra = {"dia_vencimento": 5, "primeiro_vencimento": "proximo", "entrada_prazo_dias": 0}
        geral = {"dia_vencimento": 15, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}
        outra = cria_pessoa("outra@sejaap.com.br", Papel.DIRETORIA)
        self.cliente.put(
            "/api/cobranca",
            {"geral": geral, "excecoes": [{"categoria_id": "elite", "produto_id": "pro", **regra}]},
            format="json",
        )
        segunda = APIClient()
        segunda.force_authenticate(user=outra)
        resposta = segunda.put("/api/cobranca", {"geral": geral, "excecoes": []}, format="json")
        self.assertEqual(resposta.status_code, 200, resposta.content)

        fechadas = Vigencia.objects.filter(chave="pro", campo__in=vigencias.CAMPOS_DE_COBRANCA)
        self.assertEqual(fechadas.count(), 3)
        for vigencia in fechadas:
            self.assertIsNotNone(vigencia.vigente_ate)
            self.assertEqual(vigencia.autor, self.diretoria)
            self.assertEqual(vigencia.encerrado_por, outra)
            self.assertEqual(vigencia.encerrado_por_email, "outra@sejaap.com.br")
        self.assertFalse(PoliticaCobranca.objects.filter(produto__slug="pro").exists())

    def test_remover_a_excecao_nao_fecha_o_preco(self):
        vigencias.encerrar("pro", autor=self.diretoria)
        self.assertTrue(
            Vigencia.objects.filter(chave="pro", campo="mensalidade", vigente_ate__isnull=True).exists()
        )

    def test_troca_de_valor_grava_quem_fechou_o_periodo_anterior(self):
        produto = Produto.objects.get(categoria__slug="elite", slug="pro")
        aberta = Vigencia.objects.get(chave="pro", campo="mensalidade", vigente_ate__isnull=True)
        vigencias.registrar("pro", produto.nome, "mensalidade", Decimal("1.00"), autor=self.diretoria)
        aberta.refresh_from_db()
        self.assertEqual(aberta.encerrado_por, self.diretoria)


class ReaisTest(TestCase):
    def test_formato_brasileiro(self):
        self.assertEqual(reais(Decimal("10497")), "R$ 10.497,00")
        self.assertEqual(reais(Decimal("1234567.5")), "R$ 1.234.567,50")
        self.assertEqual(reais(Decimal("0.99")), "R$ 0,99")
        self.assertEqual(reais(None), "—")
