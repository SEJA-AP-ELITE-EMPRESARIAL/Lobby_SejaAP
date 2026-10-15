"""
Departamento da venda por categoria (TSK-1059, ADR-002 §§13.3 e 13.5).

Em ordem de dano:

1. O deploy não muda o que o consultor vê: a migration 0004 copia a escolha de
   hoje para cada categoria, e a rota pública responde a mesma coisa, com a
   mesma versão.
2. Cada categoria tem a sua escolha, e a regra do fixo inativo vale para cada
   uma separadamente.
3. Um lobby com o HTML de antes, quando as categorias divergem, vê a lista
   completa — nunca o fixo de outra categoria.
4. O /admin de hoje continua funcionando: o corpo antigo vale para todas.
"""
import hashlib
import json
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalogo.tests_escrita import cria_pessoa
from apps.contas.models import Papel

from .models import ConfiguracaoDepartamento
from .servicos import sincronizar
from .tests_departamentos import dep

ROTA = "/api/departamentos/configuracao"
LISTA = [dep("1", "1.Consultoria", "001.001"), dep("2", "2.Comercial", "001.009"), dep("3", "Copa", "001.017", ativo=False)]
CATEGORIAS = ["elite", "treinamentos", "apn", "produtos"]


def versao_de_antes(itens, fixo=False):
    """A conta da TSK-585, copiada à mão."""
    chave = [list(i) for i in itens]
    if fixo:
        chave = ["fixo", chave]
    return hashlib.sha256(json.dumps(chave, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


class Base(TestCase):
    def setUp(self):
        sincronizar(LISTA)
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.diretoria)

    def publico(self):
        return APIClient().get("/api/departamentos").json()

    def salva(self, corpo):
        return self.cliente.put(ROTA, corpo, format="json")

    def codigos(self, corpo, slug):
        return [d["codigo"] for d in corpo["categorias"][slug]["departamentos"]]


class SemConfiguracaoTest(Base):
    def test_toda_categoria_ve_a_lista_e_a_versao_e_a_de_antes(self):
        corpo = self.publico()
        self.assertEqual(list(corpo["categorias"]), CATEGORIAS)
        for slug in CATEGORIAS:
            self.assertEqual(corpo["categorias"][slug]["modo"], "lista")
            self.assertEqual(self.codigos(corpo, slug), ["1", "2"])
        self.assertEqual(corpo["modo"], "lista")
        ativos = [("1", "1.Consultoria", "001.001"), ("2", "2.Comercial", "001.009")]
        self.assertEqual(corpo["versao"], versao_de_antes(ativos))

    def test_aba_traz_cada_categoria_sem_autor(self):
        corpo = self.cliente.get(ROTA).json()
        self.assertEqual(
            corpo["categorias"]["elite"],
            {"nome": "Elite", "modo": "lista", "modo_em_vigor": "lista", "fixo": None,
             "alterado_em": None, "alterado_por": None},
        )
        self.assertFalse(corpo["categorias_divergem"])


class PorCategoriaTest(Base):
    def test_cada_categoria_com_a_sua_escolha(self):
        resposta = self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}, "apn": {"modo": "lista"}}})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        corpo = self.publico()
        self.assertEqual(corpo["categorias"]["elite"]["modo"], "fixo")
        self.assertEqual(self.codigos(corpo, "elite"), ["2"])
        self.assertEqual(corpo["categorias"]["apn"]["modo"], "lista")
        self.assertEqual(self.codigos(corpo, "apn"), ["1", "2"])
        self.assertEqual(self.codigos(corpo, "produtos"), ["1", "2"])

    def test_lobby_antigo_ve_a_lista_quando_as_categorias_divergem(self):
        """Nunca o fixo da Elite numa venda da APN."""
        lista = self.publico()
        self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}}})
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "lista")
        self.assertEqual([d["codigo"] for d in corpo["departamentos"]], ["1", "2"])
        # Mas a versão muda: o lobby novo precisa saber que a Elite travou.
        self.assertNotEqual(corpo["versao"], lista["versao"])

    def test_todas_fixas_no_mesmo_tem_a_versao_de_antes(self):
        self.salva({"categorias": {s: {"modo": "fixo", "codigo": "1"} for s in CATEGORIAS}})
        corpo = self.publico()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(corpo["versao"], versao_de_antes([("1", "1.Consultoria", "001.001")], fixo=True))

    def test_categoria_ausente_nao_muda(self):
        self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}}})
        self.salva({"categorias": {"apn": {"modo": "fixo", "codigo": "1"}}})
        corpo = self.publico()
        self.assertEqual(self.codigos(corpo, "elite"), ["2"])
        self.assertEqual(self.codigos(corpo, "apn"), ["1"])

    def test_so_a_categoria_que_mudou_ganha_linha(self):
        self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}, "apn": {"modo": "lista"}}})
        self.assertEqual(list(ConfiguracaoDepartamento.objects.values_list("categoria__slug", flat=True)), ["elite"])
        self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}}})
        self.assertEqual(ConfiguracaoDepartamento.objects.count(), 1)

    def test_aba_mostra_a_divergencia_e_o_autor_de_cada_uma(self):
        self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}}})
        corpo = self.cliente.get(ROTA).json()
        self.assertTrue(corpo["categorias_divergem"])
        self.assertEqual(corpo["modo"], "lista")
        self.assertIsNone(corpo["fixo"])
        elite = corpo["categorias"]["elite"]
        self.assertEqual(elite["modo"], "fixo")
        self.assertEqual(elite["fixo"], {"codigo": "2", "descricao": "2.Comercial", "estrutura": "001.009", "ativo": True})
        self.assertEqual(elite["alterado_por"], "diretoria@sejaap.com.br")
        self.assertIsNone(corpo["categorias"]["apn"]["alterado_por"])


class FixoInativoPorCategoriaTest(Base):
    def test_o_fixo_inativo_so_solta_a_categoria_dele(self):
        self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}, "produtos": {"modo": "fixo", "codigo": "1"}}})
        sincronizar([dep("1", "1.Consultoria", "001.001"), dep("2", "2.Comercial", "001.009", ativo=False)])
        corpo = self.publico()
        self.assertEqual(corpo["categorias"]["elite"]["modo"], "lista")
        self.assertEqual(self.codigos(corpo, "elite"), ["1"])
        self.assertEqual(corpo["categorias"]["produtos"]["modo"], "fixo")
        self.assertEqual(self.codigos(corpo, "produtos"), ["1"])

        aba = self.cliente.get(ROTA).json()["categorias"]
        self.assertEqual((aba["elite"]["modo"], aba["elite"]["modo_em_vigor"]), ("fixo", "lista"))
        self.assertFalse(aba["elite"]["fixo"]["ativo"])
        self.assertEqual((aba["produtos"]["modo"], aba["produtos"]["modo_em_vigor"]), ("fixo", "fixo"))


class CorpoAntigoTest(Base):
    def test_corpo_do_admin_de_hoje_vale_para_todas(self):
        self.salva({"categorias": {"elite": {"modo": "fixo", "codigo": "2"}}})
        resposta = self.salva({"modo": "fixo", "codigo": "1"})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        corpo = self.publico()
        for slug in CATEGORIAS:
            self.assertEqual(self.codigos(corpo, slug), ["1"], slug)
        self.assertFalse(resposta.json()["categorias_divergem"])
        self.assertEqual(resposta.json()["modo"], "fixo")


class RecusaTest(Base):
    def assertRecusa(self, corpo, trecho):
        resposta = self.salva(corpo)
        self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertIn(trecho, resposta.json()["erro"])
        self.assertFalse(ConfiguracaoDepartamento.objects.exists())

    def test_categoria_desconhecida_recusa_tudo(self):
        self.assertRecusa(
            {"categorias": {"elite": {"modo": "fixo", "codigo": "1"}, "xpto": {"modo": "lista"}}}, "xpto"
        )

    def test_escolha_invalida_diz_a_categoria(self):
        self.assertRecusa({"categorias": {"apn": {"modo": "fixo"}}}, "em APN")
        self.assertRecusa({"categorias": {"apn": {"modo": "fixo", "codigo": "3"}}}, "inativo no Omie")

    def test_os_dois_corpos_juntos(self):
        self.assertRecusa({"modo": "lista", "categorias": {}}, "não os dois")

    def test_categorias_que_nao_e_objeto(self):
        self.assertRecusa({"categorias": ["elite"]}, "objeto")


class MigracaoDeDadoTest(TransactionTestCase):
    serialized_rollback = True
    antes = [("departamentos", "0003_configuracao_por_categoria")]
    depois = [("departamentos", "0004_cada_categoria_com_a_escolha_de_hoje")]

    def migra(self, alvo):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(alvo)
        return executor.loader.project_state(alvo).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_copia_a_escolha_de_hoje_com_autor_e_data_e_volta(self):
        apps = self.migra(self.antes)
        Departamento = apps.get_model("departamentos", "Departamento")
        Configuracao = apps.get_model("departamentos", "ConfiguracaoDepartamento")
        User = apps.get_model("auth", "User")
        pessoa = User.objects.create(username="gustavo@exemplo.com.br", email="gustavo@exemplo.com.br")
        agora = timezone.now()
        comercial = Departamento.objects.create(codigo="2", descricao="2.Comercial", estrutura="001.009", visto_em=agora)
        Departamento.objects.create(codigo="1", descricao="1.Consultoria", estrutura="001.001", visto_em=agora)
        # Duas gravações de antes do deploy; vale a mais recente, o fixo.
        velha = Configuracao.objects.create(modo="lista", autor=pessoa, autor_email=pessoa.email)
        Configuracao.objects.filter(pk=velha.pk).update(criado_em=agora - timedelta(days=40))
        original = Configuracao.objects.create(
            modo="fixo", departamento=comercial, autor=pessoa, autor_email=pessoa.email
        )
        quando = agora - timedelta(days=20)
        Configuracao.objects.filter(pk=original.pk).update(criado_em=quando)

        apps = self.migra(self.depois)
        Configuracao = apps.get_model("departamentos", "ConfiguracaoDepartamento")
        Categoria = apps.get_model("catalogo", "Categoria")
        copias = Configuracao.objects.filter(categoria__isnull=False)
        self.assertEqual(copias.count(), Categoria.objects.count())
        for copia in copias:
            self.assertEqual((copia.modo, copia.departamento_id), ("fixo", comercial.pk))
            self.assertEqual(copia.autor_id, pessoa.pk)
            self.assertEqual(copia.criado_em, quando)
            self.assertEqual(copia.copiada_de_id, original.pk)

        # O consultor vê o mesmo que via, com a mesma versão.
        corpo = APIClient().get("/api/departamentos").json()
        self.assertEqual(corpo["modo"], "fixo")
        self.assertEqual(corpo["versao"], versao_de_antes([("2", "2.Comercial", "001.009")], fixo=True))

        # O /admin antigo soltou a lista para todas depois do deploy; desfazer
        # preserva isso numa gravação sem categoria.
        for categoria in Categoria.objects.all():
            Configuracao.objects.create(modo="lista", categoria=categoria, autor_id=pessoa.pk, autor_email=pessoa.email)
        apps = self.migra(self.antes)
        Configuracao = apps.get_model("departamentos", "ConfiguracaoDepartamento")
        self.assertFalse(Configuracao.objects.filter(categoria__isnull=False).exists())
        ultima = Configuracao.objects.order_by("-criado_em", "-id").first()
        self.assertEqual((ultima.modo, ultima.departamento_id, ultima.autor_id), ("lista", None, pessoa.pk))

    def test_sem_escolha_antes_nao_cria_nada(self):
        self.migra(self.antes)
        apps = self.migra(self.depois)
        self.assertFalse(apps.get_model("departamentos", "ConfiguracaoDepartamento").objects.exists())
