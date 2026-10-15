"""
O dia do vencimento com liga/desliga — `PoliticaCobranca.dia_fixo` (TSK-1077).

O que estes testes guardam, em ordem de dano:

1. O deploy não muda a data de venda nenhuma: a migration 0017 liga o dia fixo
   em toda regra que existe, e o catálogo serve o resto igual a antes.
2. O /admin antigo, que não mostra o campo, não o muda ao salvar — nem nas
   categorias (pela regra única dele), nem nas exceções que ele devolve.
3. Corpo sem `dia_fixo` mantém o gravado; regra nova sem ele nasce ligada.
4. A mudança tem autor na linha do tempo e aparece no histórico unificado como
   "ligado → desligado"; a semente da migration não aparece como evento.

As datas que o desligado produz são do `index.html` (o backend confere só os
valores da venda) — conferidas em `tests/front/cronograma.test.mjs`, no CI, e no
QA de tela da TSK.
"""
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.contas.models import Papel
from apps.integracao.historico import consulta

from .models import Categoria, PoliticaCobranca, Vigencia
from .tests_escrita import cria_pessoa

REGRA = {"dia_vencimento": 15, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}
CATEGORIAS = ["elite", "treinamentos", "apn", "produtos"]


class Base(TestCase):
    def setUp(self):
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.cliente = APIClient()
        self.cliente.force_authenticate(user=self.diretoria)

    def publica(self, corpo):
        resposta = self.cliente.put("/api/cobranca", corpo, format="json")
        return resposta

    def catalogo(self):
        return APIClient().get("/api/catalogo").json()

    def da_categoria(self, slug):
        return next(c for c in self.catalogo()["cats"] if c["id"] == slug)["cobranca"]

    def do_produto(self, categoria, slug):
        cat = next(c for c in self.catalogo()["cats"] if c["id"] == categoria)
        return next(p for p in cat["products"] if p["id"] == slug).get("cobranca")

    def dia_fixo_de(self, **alvo):
        return PoliticaCobranca.objects.get(**alvo).dia_fixo

    def desliga_elite_e_o_pro(self):
        resposta = self.publica({
            "categorias": {"elite": {**REGRA, "dia_fixo": False}},
            "excecoes": [{"categoria_id": "elite", "produto_id": "pro", **REGRA, "dia_vencimento": 5, "dia_fixo": False}],
        })
        self.assertEqual(resposta.status_code, 200, resposta.content)


class DiaDoDeployTest(Base):
    def test_toda_regra_nasce_com_o_dia_fixo_ligado(self):
        self.assertTrue(PoliticaCobranca.objects.exists())
        self.assertFalse(PoliticaCobranca.objects.filter(dia_fixo=False).exists())

    def test_o_catalogo_serve_dia_fixo_em_cada_categoria(self):
        corpo = self.catalogo()
        self.assertEqual(corpo["cobranca"], {**REGRA, "dia_fixo": True})
        for categoria in corpo["cats"]:
            self.assertEqual(categoria["cobranca"], {**REGRA, "dia_fixo": True}, categoria["id"])

    def test_a_cobranca_serve_dia_fixo_em_cada_categoria_e_excecao(self):
        self.publica({"categorias": {}, "excecoes": [{"categoria_id": "elite", "produto_id": "pro", **REGRA}]})
        corpo = self.cliente.get("/api/cobranca").json()
        self.assertEqual(list(corpo["categorias"]), CATEGORIAS)
        for slug, regra in corpo["categorias"].items():
            self.assertIs(regra["dia_fixo"], True, slug)
        self.assertEqual([e["dia_fixo"] for e in corpo["excecoes"]], [True])

    def test_a_semente_abre_a_linha_do_tempo_sem_autor_na_data_da_regra(self):
        for chave in ["geral"] + [f"categoria:{slug}" for slug in CATEGORIAS]:
            with self.subTest(chave=chave):
                semente = Vigencia.objects.get(chave=chave, campo="dia_fixo", vigente_ate__isnull=True)
                self.assertEqual(semente.valor, "sim")
                self.assertIsNone(semente.autor)
                self.assertEqual(semente.autor_email, "")
                dia = Vigencia.objects.get(chave=chave, campo="dia_vencimento", vigente_ate__isnull=True)
                self.assertEqual(semente.vigente_de, dia.vigente_de)


class DesligarTest(Base):
    def test_desliga_so_a_categoria_mandada(self):
        resposta = self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": False}}})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.da_categoria("elite"), {**REGRA, "dia_fixo": False})
        for slug in ("treinamentos", "apn", "produtos"):
            self.assertIs(self.da_categoria(slug)["dia_fixo"], True, slug)
        # A geral fica como está: o corpo novo não a toca.
        self.assertTrue(PoliticaCobranca.geral_atual().dia_fixo)

    def test_desligado_guarda_o_dia_e_a_primeira_parcela(self):
        regra = {"dia_vencimento": 7, "primeiro_vencimento": "proximo", "entrada_prazo_dias": 2, "dia_fixo": False}
        self.publica({"categorias": {"apn": regra}})
        politica = PoliticaCobranca.objects.get(categoria__slug="apn")
        self.assertEqual(politica.como_dicionario(), regra)
        # E religar devolve o dia guardado, sem precisar digitá-lo de novo.
        self.publica({"categorias": {"apn": {**regra, "dia_fixo": True}}})
        self.assertEqual(self.da_categoria("apn"), {**regra, "dia_fixo": True})

    def test_excecao_desligada_vence_a_categoria_ligada(self):
        excecao = {"categoria_id": "elite", "produto_id": "evo", **REGRA, "dia_fixo": False}
        self.publica({"categorias": {}, "excecoes": [excecao]})
        self.assertIs(self.da_categoria("elite")["dia_fixo"], True)
        self.assertIs(self.do_produto("elite", "evo")["dia_fixo"], False)
        self.assertIsNone(self.do_produto("elite", "pro"))

    def test_so_o_dia_fixo_diferente_nao_e_categoria_divergente(self):
        """O aviso do /admin antigo diz que salvar lá sobrescreve as categorias;
        o dia fixo ele não sobrescreve, então não conta."""
        corpo = self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": False}}}).json()
        self.assertFalse(corpo["categorias_divergem"])

    def test_mudanca_entra_na_linha_do_tempo_com_autor(self):
        self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": False}}})
        nova = Vigencia.objects.get(chave="categoria:elite", campo="dia_fixo", vigente_ate__isnull=True)
        self.assertEqual((nova.valor, nova.autor, nova.autor_email), ("não", self.diretoria, "diretoria@sejaap.com.br"))
        semente = Vigencia.objects.get(chave="categoria:elite", campo="dia_fixo", vigente_ate=nova.vigente_de)
        self.assertEqual((semente.valor, semente.encerrado_por), ("sim", self.diretoria))
        # Só o que mudou: os outros campos da Elite não ganharam registro novo.
        self.assertEqual(
            list(Vigencia.objects.filter(chave="categoria:elite", autor=self.diretoria).values_list("campo", flat=True)),
            ["dia_fixo"],
        )

    def test_mesmo_valor_nao_grava_nada(self):
        antes = Vigencia.objects.count()
        self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": True}}})
        self.assertEqual(Vigencia.objects.count(), antes)

    def test_remover_a_excecao_fecha_o_dia_fixo_junto(self):
        self.desliga_elite_e_o_pro()
        self.publica({"categorias": {}, "excecoes": []})
        self.assertFalse(Vigencia.objects.filter(chave="pro", campo="dia_fixo", vigente_ate__isnull=True).exists())


class SemDiaFixoNoCorpoTest(Base):
    def test_categoria_que_ja_existe_mantem_o_gravado(self):
        self.desliga_elite_e_o_pro()
        self.publica({"categorias": {"elite": {**REGRA, "dia_vencimento": 20}}})
        self.assertEqual(self.da_categoria("elite"), {**REGRA, "dia_vencimento": 20, "dia_fixo": False})

    def test_excecao_que_ja_existe_mantem_o_gravado(self):
        self.desliga_elite_e_o_pro()
        self.publica({"categorias": {}, "excecoes": [{"categoria_id": "elite", "produto_id": "pro", **REGRA}]})
        self.assertIs(self.dia_fixo_de(produto__slug="pro"), False)

    def test_excecao_nova_nasce_ligada(self):
        self.publica({"categorias": {}, "excecoes": [{"categoria_id": "elite", "produto_id": "evo", **REGRA}]})
        self.assertIs(self.dia_fixo_de(produto__slug="evo"), True)
        semente = Vigencia.objects.get(chave="evo", campo="dia_fixo")
        self.assertEqual((semente.valor, semente.autor), ("sim", self.diretoria))

    def test_regra_nova_de_categoria_nasce_ligada(self):
        Categoria.objects.create(slug="mentorias", nome="Mentorias", ordem=99)
        self.publica({"categorias": {"mentorias": REGRA}})
        self.assertIs(self.dia_fixo_de(categoria__slug="mentorias"), True)


class AdminAntigoTest(Base):
    """O corpo `geral` do /admin de hoje, que não mostra o dia fixo."""

    def salva_como_o_admin(self, **mudanca_na_geral):
        """O que o `admin.html` faz: lê, mexe na geral e devolve as exceções que leu."""
        dados = self.cliente.get("/api/cobranca").json()
        corpo = {"geral": {**dados["geral"], **mudanca_na_geral}, "excecoes": dados["excecoes"]}
        resposta = self.publica(corpo)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        return resposta.json()

    def test_salvar_nao_muda_o_dia_fixo_das_categorias_nem_das_excecoes(self):
        self.desliga_elite_e_o_pro()
        self.salva_como_o_admin(dia_vencimento=20)
        self.assertEqual(self.da_categoria("elite"), {**REGRA, "dia_vencimento": 20, "dia_fixo": False})
        self.assertIs(self.da_categoria("apn")["dia_fixo"], True)
        self.assertIs(self.dia_fixo_de(produto__slug="pro"), False)
        self.assertEqual(self.do_produto("elite", "pro")["dia_vencimento"], 5)

    def test_dia_fixo_no_corpo_antigo_e_ignorado(self):
        """Uma leitura velha (feita antes de o Conecta desligar) não religa nada."""
        self.desliga_elite_e_o_pro()
        resposta = self.publica({
            "geral": {**REGRA, "dia_fixo": True},
            "excecoes": [{"categoria_id": "elite", "produto_id": "pro", **REGRA, "dia_fixo": True}],
        })
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertIs(self.dia_fixo_de(categoria__slug="elite"), False)
        self.assertIs(self.dia_fixo_de(produto__slug="pro"), False)
        # Nem desliga: a geral e as outras categorias seguem ligadas.
        self.publica({"geral": {**REGRA, "dia_fixo": False}, "excecoes": []})
        self.assertTrue(PoliticaCobranca.geral_atual().dia_fixo)
        self.assertIs(self.dia_fixo_de(categoria__slug="apn"), True)

    def test_excecao_acrescentada_pelo_admin_nasce_ligada(self):
        dados = self.cliente.get("/api/cobranca").json()
        # O `acrescentaExcecao` do admin.html copia a geral inteira para a exceção.
        nova = {"categoria_id": "elite", "produto_id": "evo", "produto_nome": "Elite · ELITE EVO", **dados["geral"]}
        self.publica({"geral": dados["geral"], "excecoes": [nova]})
        self.assertIs(self.dia_fixo_de(produto__slug="evo"), True)

    def test_salvar_sem_mudanca_nao_grava_vigencia(self):
        self.desliga_elite_e_o_pro()
        antes = Vigencia.objects.count()
        self.salva_como_o_admin()
        self.assertEqual(Vigencia.objects.count(), antes)


class RecusaTest(Base):
    def test_dia_fixo_que_nao_e_booleano(self):
        for valor in ("false", "true", 0, 1, None, [], {}):
            with self.subTest(valor=valor):
                resposta = self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": valor}}})
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("Dia do vencimento fixo", resposta.json()["erro"])
                self.assertIn("Elite", resposta.json()["erro"])
                resposta = self.publica({
                    "categorias": {},
                    "excecoes": [{"categoria_id": "elite", "produto_id": "pro", **REGRA, "dia_fixo": valor}],
                })
                self.assertEqual(resposta.status_code, 400)
        self.assertIs(self.dia_fixo_de(categoria__slug="elite"), True)
        self.assertFalse(PoliticaCobranca.objects.filter(produto__slug="pro").exists())

    def test_desligado_continua_exigindo_o_dia_guardado(self):
        resposta = self.publica({"categorias": {"elite": {"primeiro_vencimento": "mes_seguinte", "dia_fixo": False}}})
        self.assertEqual(resposta.status_code, 400)
        self.assertIs(self.dia_fixo_de(categoria__slug="elite"), True)


class HistoricoUnificadoTest(Base):
    def cobranca(self, **filtros):
        return consulta({"area": "cobranca", **filtros})["itens"]

    def test_desligar_aparece_com_de_para_e_autor(self):
        self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": False}}})
        evento = self.cobranca(categoria="elite")[0]
        self.assertEqual(evento["titulo"], "Regra de cobrança · Elite")
        self.assertEqual(evento["autor"], "diretoria@sejaap.com.br")
        self.assertEqual(
            evento["alteracoes"], ["dia do vencimento fixo: ligado → desligado (parcelas no dia da venda)"]
        )

    def test_religar_aparece_ao_contrario(self):
        self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": False}}})
        self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": True}}})
        evento = self.cobranca(categoria="elite")[0]
        self.assertEqual(
            evento["alteracoes"], ["dia do vencimento fixo: desligado (parcelas no dia da venda) → ligado"]
        )

    def test_a_semente_da_migration_nao_vira_evento(self):
        """Sem pessoa e sem mudança: nada novo no histórico no dia do deploy."""
        for item in self.cobranca():
            with self.subTest(titulo=item["titulo"]):
                self.assertFalse(any("dia do vencimento fixo" in a for a in item["alteracoes"]))

    def test_excecao_nova_mostra_o_dia_fixo_sem_de(self):
        excecao = {"categoria_id": "elite", "produto_id": "evo", **REGRA, "dia_fixo": False}
        self.publica({"categorias": {}, "excecoes": [excecao]})
        evento = self.cobranca(produto="evo")[0]
        self.assertEqual(evento["titulo"], "Exceção de cobrança · ELITE EVO")
        self.assertIn("dia do vencimento fixo: desligado (parcelas no dia da venda)", evento["alteracoes"])

    def test_remocao_de_excecao_e_um_evento_so(self):
        self.desliga_elite_e_o_pro()
        self.publica({"categorias": {}, "excecoes": []})
        titulos = [i["titulo"] for i in self.cobranca(produto="pro")]
        self.assertEqual(titulos, ["Exceção de cobrança removida · ELITE PRO", "Exceção de cobrança · ELITE PRO"])

    def test_mudar_pelo_django_admin_entra_na_linha_do_tempo(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        politica = PoliticaCobranca.objects.get(categoria__slug="elite")
        politica.dia_fixo = False
        pedido = RequestFactory().post("/")
        pedido.user = self.diretoria
        formulario = type("Form", (), {"changed_data": ["dia_fixo"]})()
        modelo_admin = django_admin.site._registry[PoliticaCobranca]
        modelo_admin.save_model(pedido, politica, formulario, True)
        # O alvo não se troca depois de criada a regra.
        self.assertEqual(modelo_admin.get_readonly_fields(pedido, politica), ("geral", "produto", "categoria"))
        self.assertEqual(modelo_admin.get_readonly_fields(pedido, None), ())

        aberta = Vigencia.objects.get(chave="categoria:elite", campo="dia_fixo", vigente_ate__isnull=True)
        self.assertEqual((aberta.valor, aberta.autor), ("não", self.diretoria))
        # E o Conecta religando depois é registrado, com o de → para certo.
        self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": True}}})
        evento = self.cobranca(categoria="elite")[0]
        self.assertEqual(
            evento["alteracoes"], ["dia do vencimento fixo: desligado (parcelas no dia da venda) → ligado"]
        )

    def test_pagina_do_django_admin_abre_com_o_alvo_so_leitura(self):
        from django.contrib.auth.models import User

        admin_local = User.objects.create_superuser("conserto", "conserto@sejaap.com.br", "x")
        cliente = APIClient()
        cliente.force_login(admin_local)
        politica = PoliticaCobranca.objects.get(categoria__slug="elite")
        resposta = cliente.get(f"/django-admin/catalogo/politicacobranca/{politica.pk}/change/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'name="dia_fixo"')
        self.assertNotContains(resposta, 'name="categoria"')
        self.assertEqual(cliente.get("/django-admin/catalogo/politicacobranca/add/").status_code, 200)

    def test_historico_antigo_mostra_o_campo_com_rotulo(self):
        """O `/api/historico` do /admin antigo: o registro novo sai com rótulo, sem quebrar."""
        self.publica({"categorias": {"elite": {**REGRA, "dia_fixo": False}}})
        registros = self.cliente.get("/api/historico?chave=categoria:elite&campo=dia_fixo").json()["registros"]
        self.assertEqual([(r["valor"], r["campo_rotulo"]) for r in registros][0], ("não", "Dia do vencimento fixo"))


class MigracaoDoDiaFixoTest(TransactionTestCase):
    """A 0017 ida e volta, num banco migrado de verdade."""

    serialized_rollback = True
    antes = [("catalogo", "0016_semente_da_trava_de_categoria")]
    depois = [("catalogo", "0017_dia_fixo_ligado_em_toda_regra")]

    def migra(self, alvo):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(alvo)
        return executor.loader.project_state(alvo).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def prepara_o_banco_de_antes(self):
        """Regras diferentes do padrão e uma exceção com história, no modelo de antes."""
        apps = self.migra(self.antes)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        Produto = apps.get_model("catalogo", "Produto")
        Vigencia = apps.get_model("catalogo", "Vigencia")
        Politica.objects.filter(categoria__slug="apn").update(
            dia_vencimento=7, primeiro_vencimento="proximo", entrada_prazo_dias=3
        )
        pro = Produto.objects.get(categoria__slug="elite", slug="pro")
        excecao = Politica.objects.create(
            geral=False, produto=pro, dia_vencimento=5, primeiro_vencimento="mes_seguinte", entrada_prazo_dias=0
        )
        self.criada_em = timezone.now() - timedelta(days=40)
        for campo, valor in (("dia_vencimento", "5"), ("primeiro_vencimento", "mes_seguinte"), ("entrada_prazo_dias", "0")):
            Vigencia.objects.create(
                chave="pro", rotulo="ELITE PRO · cobrança", campo=campo, valor=valor,
                vigente_de=self.criada_em, autor_email="alguem@sejaap.com.br",
            )
        self.regras_de_antes = {
            p.pk: (p.dia_vencimento, p.primeiro_vencimento, p.entrada_prazo_dias) for p in Politica.objects.all()
        }
        self.vigencias_de_antes = Vigencia.objects.count()
        return excecao.pk

    def test_ida_liga_tudo_e_o_resto_fica_igual(self):
        pk_da_excecao = self.prepara_o_banco_de_antes()

        apps = self.migra(self.depois)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        Vigencia = apps.get_model("catalogo", "Vigencia")
        self.assertEqual(Politica.objects.count(), len(self.regras_de_antes))
        for politica in Politica.objects.all():
            self.assertTrue(politica.dia_fixo, politica.pk)
            self.assertEqual(
                (politica.dia_vencimento, politica.primeiro_vencimento, politica.entrada_prazo_dias),
                self.regras_de_antes[politica.pk],
            )

        # Uma semente por regra, sem autor, e nada mais mudou na linha do tempo.
        sementes = Vigencia.objects.filter(campo="dia_fixo")
        self.assertEqual(sementes.count(), len(self.regras_de_antes))
        self.assertEqual(Vigencia.objects.exclude(campo="dia_fixo").count(), self.vigencias_de_antes)
        self.assertEqual({(s.valor, s.autor_id, s.autor_email) for s in sementes}, {("sim", None, "")})
        self.assertEqual(sementes.get(chave="pro").vigente_de, self.criada_em)
        copia = Vigencia.objects.get(chave="categoria:apn", campo="dia_vencimento", vigente_ate__isnull=True)
        self.assertEqual(sementes.get(chave="categoria:apn").vigente_de, copia.vigente_de)

        # O que o consultor recebe: o mesmo de antes, com dia_fixo true em tudo.
        corpo = APIClient().get("/api/catalogo").json()
        apn = next(c for c in corpo["cats"] if c["id"] == "apn")
        self.assertEqual(
            apn["cobranca"],
            {"dia_vencimento": 7, "primeiro_vencimento": "proximo", "entrada_prazo_dias": 3, "dia_fixo": True},
        )
        elite = next(c for c in corpo["cats"] if c["id"] == "elite")
        pro = next(p for p in elite["products"] if p["id"] == "pro")
        self.assertEqual(
            pro["cobranca"],
            {"dia_vencimento": 5, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0, "dia_fixo": True},
        )
        self.assertEqual(Politica.objects.get(pk=pk_da_excecao).produto.slug, "pro")

        # A exceção criada por pessoa antes do deploy: a semente fica sozinha no
        # grupo dela (sem autor) e não pode virar evento, nem derrubar a consulta.
        eventos = consulta({"area": "cobranca", "produto": "pro"})["itens"]
        self.assertEqual([e["titulo"] for e in eventos], ["Exceção de cobrança · ELITE PRO"])
        self.assertEqual(len(eventos[0]["alteracoes"]), 3)
        self.assertFalse(any("dia do vencimento fixo" in a for a in eventos[0]["alteracoes"]))

    def test_volta_tira_a_coluna_e_a_linha_do_tempo_do_dia_fixo(self):
        self.prepara_o_banco_de_antes()
        apps = self.migra(self.depois)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        Vigencia = apps.get_model("catalogo", "Vigencia")
        # Depois do deploy alguém desligou a Elite.
        Politica.objects.filter(categoria__slug="elite").update(dia_fixo=False)
        agora = timezone.now()
        Vigencia.objects.filter(chave="categoria:elite", campo="dia_fixo").update(vigente_ate=agora)
        Vigencia.objects.create(
            chave="categoria:elite", rotulo="Elite · cobrança", campo="dia_fixo", valor="não",
            vigente_de=agora, autor_email="alguem@sejaap.com.br",
        )

        apps = self.migra(self.antes)
        Politica = apps.get_model("catalogo", "PoliticaCobranca")
        Vigencia = apps.get_model("catalogo", "Vigencia")
        self.assertNotIn("dia_fixo", [f.name for f in Politica._meta.get_fields()])
        self.assertFalse(Vigencia.objects.filter(campo="dia_fixo").exists())
        self.assertEqual(Vigencia.objects.count(), self.vigencias_de_antes)
        for politica in Politica.objects.all():
            self.assertEqual(
                (politica.dia_vencimento, politica.primeiro_vencimento, politica.entrada_prazo_dias),
                self.regras_de_antes[politica.pk],
            )

        # E ir de novo funciona: a semente volta, uma por regra.
        apps = self.migra(self.depois)
        Vigencia = apps.get_model("catalogo", "Vigencia")
        self.assertEqual(Vigencia.objects.filter(campo="dia_fixo").count(), len(self.regras_de_antes))

    def test_banco_de_antes_cria_excecao_sem_conhecer_a_coluna(self):
        """O padrão também no banco: o backend de antes (modelo sem a coluna)
        cria exceção na janela do deploy e numa volta só de imagem."""
        self.migra(self.depois)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM catalogo_produto WHERE slug = 'evo'")
            (produto_id,) = cursor.fetchone()
            agora = timezone.now()
            cursor.execute(
                "INSERT INTO catalogo_politicacobranca (geral, produto_id, categoria_id, dia_vencimento, "
                "primeiro_vencimento, entrada_prazo_dias, criado_em, atualizado_em) "
                "VALUES (%s, %s, NULL, 5, 'mes_seguinte', 0, %s, %s)",
                [False, produto_id, agora, agora],
            )
        self.assertIs(PoliticaCobranca.objects.get(produto__slug="evo").dia_fixo, True)
