"""
"Avaliações" vira "DH - Avaliação" (TSK-1056): a migration e o produto no catálogo.

A MIGRATION É TESTADA DE VERDADE

O produto não existe em banco nenhum montado só pelas migrations — nasceu pelo
/admin em produção. Então o teste volta o banco à `0011`, cria o Avaliações
como ele está lá (sigla AVL, R$ 1.500 à vista, sem fluxo, com a vigência que a
aba Produtos grava) e roda a `0012` pelo executor do Django, com os modelos
históricos. Chamar a função com os modelos de hoje provaria menos: é na forma
histórica que a migration roda no deploy.
"""
import importlib
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import IntegrityError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.vendas.models import ComprovanteVenda

from .models import Categoria
from .tests_produtos_novos import cria_produto

ANTES = [("catalogo", "0011_categoria_comeca_pelo_produto")]
DEPOIS = [("catalogo", "0012_avaliacoes_vira_dh_avaliacao")]

migracao = importlib.import_module("apps.catalogo.migrations.0012_avaliacoes_vira_dh_avaliacao")


class MigracaoDoAvaliacoesTest(TransactionTestCase):
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

    def cria_avaliacoes(self, **mudancas):
        """O Avaliações como a aba Produtos o criou em produção."""
        Categoria = self.modelos.get_model("catalogo", "Categoria")
        Produto = self.modelos.get_model("catalogo", "Produto")
        Vigencia = self.modelos.get_model("catalogo", "Vigencia")
        campos = {
            "categoria": Categoria.objects.get(slug="produtos"),
            "slug": "avaliacoes",
            "nome": "Avaliações",
            "sigla": "AVL",
            "descricao": "Avaliação de perfil",
            "duracao": "por avaliação",
            "icone": "psychology",
            "recorrente": False,
            "valor": Decimal("1500.00"),
            "ordem": 1,
        }
        campos.update(mudancas)
        produto = Produto.objects.create(**campos)
        Vigencia.objects.create(
            chave="avaliacoes", rotulo="Avaliações", campo="valor", valor="1500.00",
            vigente_de=timezone.now() - timezone.timedelta(days=30),
            autor_email="diretoria@sejaap.com.br",
        )
        return produto

    def produto(self, modelos):
        return modelos.get_model("catalogo", "Produto").objects.get(slug="avaliacoes")

    def vigencias(self, modelos):
        Vigencia = modelos.get_model("catalogo", "Vigencia")
        return list(Vigencia.objects.filter(chave="avaliacoes").order_by("vigente_de", "id"))

    def test_vira_dh_avaliacao_com_sigla_lba_e_formulario_proprio(self):
        self.cria_avaliacoes()
        depois = self.migra(DEPOIS)

        produto = self.produto(depois)
        self.assertEqual(produto.nome, "DH - Avaliação")
        self.assertEqual(produto.sigla, "LBA")
        self.assertEqual(produto.fluxo, "dh_avaliacao")
        # O identificador fica: ele viaja no payload e é a chave do histórico.
        self.assertEqual(produto.slug, "avaliacoes")
        self.assertEqual(produto.categoria.slug, "produtos")
        # Produto de formulário não guarda preço (a constraint da 0009).
        self.assertIsNone(produto.valor)
        self.assertIsNone(produto.mensalidade)
        self.assertIsNone(produto.vigencia_meses)
        self.assertFalse(produto.recorrente)
        # O que não é dele não muda.
        self.assertEqual(produto.descricao, "Avaliação de perfil")
        self.assertEqual(produto.icone, "psychology")

    def test_o_preco_antigo_fecha_na_linha_do_tempo_sem_sumir(self):
        """Aberta, a vigência diria na aba Histórico que ele ainda custa 1.500."""
        self.cria_avaliacoes()
        depois = self.migra(DEPOIS)

        [vigencia] = self.vigencias(depois)
        self.assertEqual(vigencia.valor, "1500.00")
        self.assertIsNotNone(vigencia.vigente_ate)

    def test_recrutamento_e_selecao_nao_muda(self):
        self.cria_avaliacoes()
        antes = self.modelos.get_model("catalogo", "Produto").objects.get(slug="recrutamento-selecao")
        depois = self.migra(DEPOIS).get_model("catalogo", "Produto").objects.get(slug="recrutamento-selecao")
        for campo in ("nome", "sigla", "fluxo", "valor", "descricao", "ordem"):
            self.assertEqual(getattr(depois, campo), getattr(antes, campo), campo)

    def test_vendas_antigas_com_protocolo_avl_nao_mudam(self):
        self.cria_avaliacoes()
        venda = ComprovanteVenda.objects.create(
            protocolo="AVL-261001012345", fluxo="elite",
            valores={"categoria_id": "produtos", "produto_id": "avaliacoes", "valor_total": "1500.00"},
            hash_valores="0" * 64, expira_em=timezone.now(),
        )
        self.migra(DEPOIS)

        venda.refresh_from_db()
        self.assertEqual(venda.protocolo, "AVL-261001012345")
        self.assertEqual(venda.fluxo, "elite")
        self.assertEqual(venda.valores["valor_total"], "1500.00")

    def test_banco_sem_o_produto_nao_muda_nada(self):
        Produto = self.modelos.get_model("catalogo", "Produto")
        antes = list(Produto.objects.order_by("id").values())
        depois = self.migra(DEPOIS).get_model("catalogo", "Produto")
        self.assertEqual(list(depois.objects.order_by("id").values()), antes)
        self.assertFalse(depois.objects.filter(sigla="LBA").exists())

    def test_desfazer_devolve_o_produto_de_tabela(self):
        self.cria_avaliacoes(valor=Decimal("1650.00"))
        Vigencia = self.modelos.get_model("catalogo", "Vigencia")
        Vigencia.objects.filter(chave="avaliacoes").update(valor="1650.00")
        self.migra(DEPOIS)
        antes = self.migra(ANTES)

        produto = self.produto(antes)
        self.assertEqual(produto.nome, "Avaliações")
        self.assertEqual(produto.sigla, "AVL")
        self.assertEqual(produto.fluxo, "")
        self.assertFalse(produto.recorrente)
        # O valor volta da linha do tempo, não do número do pedido.
        self.assertEqual(produto.valor, Decimal("1650.00"))

        # A volta entra como vigência nova; a antiga continua fechada.
        antiga, nova = self.vigencias(antes)
        self.assertIsNotNone(antiga.vigente_ate)
        self.assertEqual(nova.valor, "1650.00")
        self.assertIsNone(nova.vigente_ate)

    def test_desfazer_sem_linha_do_tempo_usa_o_valor_do_pedido(self):
        self.cria_avaliacoes()
        self.modelos.get_model("catalogo", "Vigencia").objects.all().delete()
        self.migra(DEPOIS)
        self.assertEqual(self.produto(self.migra(ANTES)).valor, Decimal("1500.00"))

    def test_rodar_de_novo_nao_faz_nada(self):
        """Já convertido (à mão ou por uma rodada anterior), fica como está."""
        self.cria_avaliacoes()
        depois = self.migra(DEPOIS)
        antes = list(depois.get_model("catalogo", "Produto").objects.order_by("id").values())
        migracao.aplicar(depois, None)
        self.assertEqual(
            list(depois.get_model("catalogo", "Produto").objects.order_by("id").values()), antes
        )

    def test_sigla_lba_em_uso_para_a_migration(self):
        """Sigla repetida gera protocolos que ninguém desempata. Para, em português."""
        self.cria_avaliacoes()
        Produto = self.modelos.get_model("catalogo", "Produto")
        Produto.objects.create(
            categoria=Produto.objects.get(slug="avaliacoes").categoria, slug="outro",
            nome="Outro", sigla="LBA", valor=Decimal("10"), ordem=9,
        )
        with self.assertRaisesMessage(RuntimeError, "A sigla LBA já é de"):
            migracao.aplicar(self.modelos, None)
        self.assertEqual(self.produto(self.modelos).sigla, "AVL")
        Produto.objects.filter(slug="outro").delete()

    def test_produto_recorrente_para_a_migration(self):
        """O desfazer só sabe devolver valor à vista."""
        self.cria_avaliacoes(
            recorrente=True, valor=None, mensalidade=Decimal("150"), vigencia_meses=10
        )
        with self.assertRaisesMessage(RuntimeError, "recorrente"):
            migracao.aplicar(self.modelos, None)
        self.assertEqual(self.produto(self.modelos).fluxo, "")
        self.modelos.get_model("catalogo", "Produto").objects.filter(slug="avaliacoes").delete()


class DhAvaliacaoNoCatalogoTest(TestCase):
    """O produto convertido, do jeito que o lobby e o fallback o recebem."""

    def setUp(self):
        self.produto = cria_produto(
            categoria=Categoria.objects.get(slug="produtos"),
            slug="avaliacoes",
            nome="DH - Avaliação",
            sigla="LBA",
            descricao="Avaliação comportamental PROFILER",
            duracao="por avaliação",
            icone="psychology",
            fluxo="dh_avaliacao",
            ordem=1,
        )

    def _produtos(self):
        cats = APIClient().get("/api/catalogo").json()["cats"]
        return {p["id"]: p for p in next(c for c in cats if c["id"] == "produtos")["products"]}

    def test_sai_com_flow_proprio_e_sem_preco(self):
        produto = self._produtos()["avaliacoes"]
        self.assertEqual(produto["flow"], "dh_avaliacao")
        self.assertEqual(produto["sigla"], "LBA")
        for campo in ("price", "monthly", "recurring", "vigencia"):
            self.assertNotIn(campo, produto)

    def test_recrutamento_e_selecao_continua_dh(self):
        self.assertEqual(self._produtos()["recrutamento-selecao"]["flow"], "dh")

    def test_o_banco_recusa_preco_no_dh_avaliacao(self):
        self.produto.valor = Decimal("1500")
        with self.assertRaises(IntegrityError):
            self.produto.save()

    def test_fluxo_e_validado_contra_os_formularios_do_front(self):
        self.produto.fluxo = "dh_avaliacoes"
        with self.assertRaises(ValidationError):
            self.produto.full_clean()

    def test_fallback_do_front_sai_com_o_fluxo_novo(self):
        saida = StringIO()
        call_command("exportar_cats_do_front", stdout=saida, stderr=StringIO())
        self.assertIn(
            "{ id: 'avaliacoes', name: 'DH - Avaliação', sigla: 'LBA', "
            "desc: 'Avaliação comportamental PROFILER', duration: 'por avaliação', "
            "flow: 'dh_avaliacao', icon: 'psychology' },",
            saida.getvalue(),
        )
