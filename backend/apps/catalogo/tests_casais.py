"""
O Casais de Alta Performance no catálogo (TSK-1622): a migration `0018` e o produto.

A migration é testada de verdade, como a `0012`: o banco volta à `0017` (o
desfazer da `0018` apaga o produto), o teste monta o cenário com os modelos
históricos e roda a `aplicar` — é na forma histórica que ela roda no deploy.
"""
import importlib
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import IntegrityError
from django.test import TestCase, TransactionTestCase

from apps.contas.para_testes import cliente_logado

from .models import Produto

ANTES = [("catalogo", "0017_dia_fixo_ligado_em_toda_regra")]
DEPOIS = [("catalogo", "0018_produto_casais_de_alta_performance")]
SLUG = "casais-alta-performance"

migracao = importlib.import_module("apps.catalogo.migrations.0018_produto_casais_de_alta_performance")


class MigracaoDoCasaisTest(TransactionTestCase):
    # O banco de teste nasce com o catálogo semeado pelas migrations, e uma
    # TransactionTestCase o esvaziaria ao terminar. Isto o devolve como estava.
    serialized_rollback = True

    def setUp(self):
        self.modelos = self.migra(ANTES)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def migra(self, alvo):
        executor = MigrationExecutor(connection)
        executor.migrate(alvo)
        return MigrationExecutor(connection).loader.project_state(alvo).apps

    def produtos(self, modelos):
        return modelos.get_model("catalogo", "Produto").objects.filter(categoria__slug="produtos")

    def test_cria_o_produto_de_formulario_sem_preco_no_fim_da_lista(self):
        Produto = self.modelos.get_model("catalogo", "Produto")
        categoria = self.modelos.get_model("catalogo", "Categoria").objects.get(slug="produtos")
        # O DH - Avaliação, como está em produção (criado pelo /admin).
        Produto.objects.create(categoria=categoria, slug="avaliacoes", nome="DH - Avaliação",
                               sigla="LBA", fluxo="dh_avaliacao", ordem=4)
        self.assertFalse(self.produtos(self.modelos).filter(slug=SLUG).exists())

        depois = self.migra(DEPOIS)
        produto = self.produtos(depois).get(slug=SLUG)
        self.assertEqual(
            (produto.nome, produto.sigla, produto.fluxo, produto.recorrente),
            ("Casais de Alta Performance", "CAP", "casais", False),
        )
        self.assertEqual((produto.valor, produto.mensalidade, produto.vigencia_meses), (None, None, None))
        self.assertEqual(produto.ordem, 5)
        # Sem linha do tempo de valor: o produto não tem valor de tabela.
        Vigencia = depois.get_model("catalogo", "Vigencia")
        self.assertFalse(Vigencia.objects.filter(chave=SLUG).exists())
        Produto = depois.get_model("catalogo", "Produto")
        Produto.objects.filter(slug="avaliacoes").delete()

    def test_os_outros_produtos_nao_mudam(self):
        antes = {p.slug: (p.nome, p.sigla, p.fluxo, p.ordem) for p in self.produtos(self.modelos)}
        depois = self.migra(DEPOIS)
        self.assertEqual(
            {p.slug: (p.nome, p.sigla, p.fluxo, p.ordem) for p in self.produtos(depois).exclude(slug=SLUG)},
            antes,
        )

    def test_rodar_de_novo_nao_faz_nada(self):
        depois = self.migra(DEPOIS)
        migracao.aplicar(depois, None)
        self.assertEqual(self.produtos(depois).filter(slug=SLUG).count(), 1)

    def test_sigla_cap_em_uso_para_a_migration(self):
        """Sigla repetida gera protocolos que ninguém desempata. Para, em português."""
        Produto = self.modelos.get_model("catalogo", "Produto")
        Produto.objects.create(categoria=self.modelos.get_model("catalogo", "Categoria").objects.get(slug="elite"),
                               slug="capital", nome="Capital", sigla="CAP", valor=Decimal("10"), ordem=9)
        with self.assertRaisesMessage(RuntimeError, 'A sigla CAP já é de o produto "Capital"'):
            migracao.aplicar(self.modelos, None)
        self.assertFalse(self.produtos(self.modelos).filter(slug=SLUG).exists())
        Produto.objects.filter(slug="capital").delete()

    def test_sigla_cap_de_categoria_tambem(self):
        Categoria = self.modelos.get_model("catalogo", "Categoria")
        apn = Categoria.objects.get(slug="apn")
        sigla = apn.sigla
        apn.sigla = "CAP"
        apn.save()
        with self.assertRaisesMessage(RuntimeError, 'A sigla CAP já é de a categoria "APN"'):
            migracao.aplicar(self.modelos, None)
        apn.sigla = sigla
        apn.save()

    def test_outro_produto_com_o_mesmo_identificador_para_a_migration(self):
        Produto = self.modelos.get_model("catalogo", "Produto")
        categoria = self.modelos.get_model("catalogo", "Categoria").objects.get(slug="produtos")
        Produto.objects.create(categoria=categoria, slug=SLUG, nome="Casais (tabela)", sigla="CAS",
                               valor=Decimal("100"), ordem=7)
        with self.assertRaisesMessage(RuntimeError, "não é o formulário do Casais"):
            migracao.aplicar(self.modelos, None)
        Produto.objects.filter(slug=SLUG).delete()

    def test_criado_a_mao_com_o_fluxo_certo_fica_como_esta(self):
        Produto = self.modelos.get_model("catalogo", "Produto")
        categoria = self.modelos.get_model("catalogo", "Categoria").objects.get(slug="produtos")
        Produto.objects.create(categoria=categoria, slug=SLUG, nome="Casais AP", sigla="CAP",
                               fluxo="casais", ordem=3)
        migracao.aplicar(self.modelos, None)
        self.assertEqual(self.produtos(self.modelos).get(slug=SLUG).nome, "Casais AP")
        Produto.objects.filter(slug=SLUG).delete()

    def test_sem_a_categoria_produtos_para_a_migration(self):
        Categoria = self.modelos.get_model("catalogo", "Categoria")
        produtos = Categoria.objects.get(slug="produtos")
        produtos.slug = "produtos-renomeada"
        produtos.save()
        with self.assertRaisesMessage(RuntimeError, "A categoria Produtos"):
            migracao.aplicar(self.modelos, None)
        produtos.slug = "produtos"
        produtos.save()

    def test_desfazer_apaga_o_produto_sem_venda(self):
        self.migra(DEPOIS)
        depois_de_desfazer = self.migra(ANTES)
        self.assertFalse(self.produtos(depois_de_desfazer).filter(slug=SLUG).exists())

    def test_desfazer_com_venda_emitida_para(self):
        depois = self.migra(DEPOIS)
        ComprovanteVenda = depois.get_model("vendas", "ComprovanteVenda")
        from django.utils import timezone
        ComprovanteVenda.objects.create(
            protocolo="CAP-261125012345", fluxo="casais", valores={
                "categoria_id": "produtos", "produto_id": SLUG,
            }, hash_valores="x" * 64, expira_em=timezone.now(),
        )
        with self.assertRaisesMessage(RuntimeError, "tem venda emitida"):
            migracao.desfazer(depois, None)
        self.assertTrue(self.produtos(depois).filter(slug=SLUG).exists())
        ComprovanteVenda.objects.all().delete()


class CasaisNoCatalogoTest(TestCase):
    """O produto criado pela migration, do jeito que o lobby e o fallback o recebem."""

    def _produtos(self):
        cats = cliente_logado().get("/api/catalogo").json()["cats"]
        return {p["id"]: p for p in next(c for c in cats if c["id"] == "produtos")["products"]}

    def test_sai_com_flow_proprio_e_sem_preco(self):
        produto = self._produtos()[SLUG]
        self.assertEqual((produto["flow"], produto["sigla"], produto["name"]),
                         ("casais", "CAP", "Casais de Alta Performance"))
        for campo in ("price", "monthly", "recurring", "vigencia"):
            self.assertNotIn(campo, produto)

    def test_vem_depois_do_recrutamento_e_selecao(self):
        self.assertEqual(list(self._produtos()), ["recrutamento-selecao", SLUG])

    def test_o_banco_recusa_preco_no_casais(self):
        produto = Produto.objects.get(slug=SLUG)
        produto.valor = Decimal("39997")
        with self.assertRaises(IntegrityError):
            produto.save()

    def test_fluxo_casais_passa_na_validacao_do_formulario(self):
        produto = Produto.objects.get(slug=SLUG)
        produto.full_clean()
        produto.fluxo = "casal"
        with self.assertRaises(ValidationError):
            produto.full_clean()

    def test_fallback_do_front_e_o_que_o_comando_gera(self):
        """O `CATS` do `index.html` tem a mesma linha que o `exportar_cats_do_front` gera."""
        saida = StringIO()
        call_command("exportar_cats_do_front", stdout=saida, stderr=StringIO())
        linha = (
            "{ id: 'casais-alta-performance', name: 'Casais de Alta Performance', sigla: 'CAP', "
            "desc: 'Programa para o casal — um contrato, dois participantes', duration: 'por casal', "
            "flow: 'casais', icon: 'diversity_1' },"
        )
        self.assertIn(linha, saida.getvalue())
        index = Path(settings.BASE_DIR).parent / "index.html"
        self.assertIn(linha, index.read_text(encoding="utf-8"))
