"""
As rotas novas da integração (ADR-002 §§13.4 e 13.5), pela asserção.

    POST  /api/integracao/catalogo/publicar
    PATCH /api/integracao/categorias/<slug>
    GET   /api/integracao/historico

Os corpos de resposta são conferidos contra `backend/tests/contratos/integracao/`.
As pessoas são as fictícias do ADR: Bruna Lima (diretoria) e Rafael Souza
(gerente), com @exemplo.com.br.
"""
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from apps.catalogo.models import Categoria, Produto, PublicacaoCatalogo, Vigencia
from apps.contas import autenticacao_conecta
from apps.contas.models import Papel, VinculoIdentidade
from apps.contas.tests_assercao import BaseAssercao, ParDeTeste
from apps.departamentos.servicos import sincronizar
from apps.departamentos.tests_departamentos import dep

from .contratos import confere

PUBLICAR = "/api/integracao/catalogo/publicar"
BRUNA = "bruna.lima@exemplo.com.br"
RAFAEL = "rafael.souza@exemplo.com.br"


def pessoa(email, nome, papel):
    primeiro, ultimo = nome.split(" ", 1)
    usuario = User.objects.create_user(username=email, email=email, first_name=primeiro, last_name=ultimo)
    VinculoIdentidade.objects.create(usuario=usuario, identidade_id=uuid.uuid4(), papel=papel)
    return usuario


class Base(BaseAssercao):
    def setUp(self):
        super().setUp()
        self.bruna = pessoa(BRUNA, "Bruna Lima", Papel.DIRETORIA)
        self.rafael = pessoa(RAFAEL, "Rafael Souza", Papel.GERENTE)
        self.elite = Categoria.objects.get(slug="elite")
        self.avulso = Produto.objects.create(
            categoria=self.elite, slug="imersao", nome="IMERSÃO", sigla="IME",
            recorrente=False, valor=Decimal("5000"), ordem=99,
        )

    def como(self, metodo, caminho, corpo=None, sub=BRUNA, **claims):
        return self.chama(metodo, caminho, self.par.assina(sub, **claims), corpo)

    def publica(self, corpo, **kwargs):
        return self.como("post", PUBLICAR, corpo, **kwargs)


class ContratoDasRotasSimplesTest(Base):
    def test_quem_sou(self):
        confere(self, self.como("get", "/api/integracao/quem-sou").json(), "quem_sou")

    def test_quem_sou_de_quem_nao_publica(self):
        confere(self, self.como("get", "/api/integracao/quem-sou", sub=RAFAEL).json(), "quem_sou_so_leitura")

    def test_saude(self):
        confere(self, self.como("get", "/api/integracao/saude").json(), "saude")

    def test_erros(self):
        confere(self, self.como("get", "/api/integracao/quem-sou", sub="ninguem@exemplo.com.br").json(), "erro_sem_conta")
        confere(self, self.como("put", "/api/catalogo", {"cats": []}).json(), "erro_rota_nao_liberada")
        confere(self, self.como("get", "/api/publicacoes", sub=RAFAEL).json(), "erro_sem_permissao")
        confere(self, self.como("post", PUBLICAR, {}, imp=True, act=9).json(), "erro_impersonacao")
        with override_settings(CONECTA_ASSERCAO_CHAVES=""):
            confere(self, APIClient().get("/api/integracao/quem-sou").json(), "erro_integracao_desligada")
        # Redis recém-reiniciado: a janela do `jti` (ver MARCO_DO_CACHE).
        autenticacao_conecta._cache_do_jti().clear()
        confere(self, self.como("get", "/api/integracao/quem-sou").json(), "erro_integracao_reiniciando")


class PublicarTest(Base):
    CORPO = {
        "valores": [
            {"categoria": "elite", "produto": "pro", "monthly": 10497.00},
            {"categoria": "elite", "produto": "imersao", "price": 5500},
            {"categoria": "apn", "valor_referencia": 35000},
        ],
        "produtos": [
            {"categoria": "elite", "slug": "pro", "nome": "ELITE PRO", "sigla": "PRO",
             "descricao": "O plano da Estrutura", "duracao": "12 meses", "icone": "workspace_premium"},
        ],
    }

    def test_publica_tudo_numa_publicacao_com_a_pessoa_do_sub(self):
        antes = PublicacaoCatalogo.objects.count()
        resposta = self.publica(self.CORPO)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(PublicacaoCatalogo.objects.count(), antes + 1)

        publicacao = PublicacaoCatalogo.objects.order_by("-publicado_em", "-id").first()
        self.assertEqual(publicacao.autor, self.bruna)
        self.assertEqual(publicacao.autor_email, BRUNA)
        self.assertEqual(publicacao.alvos, "|apn|elite/imersao|elite/pro|")

        pro = Produto.objects.get(categoria__slug="elite", slug="pro")
        self.assertEqual(pro.mensalidade, Decimal("10497.00"))
        self.assertEqual(pro.descricao, "O plano da Estrutura")
        self.assertEqual(Produto.objects.get(slug="imersao").valor, Decimal("5500.00"))
        self.assertEqual(Categoria.objects.get(slug="apn").valor_referencia, Decimal("35000.00"))
        vigencia = Vigencia.objects.get(chave="pro", campo="mensalidade", vigente_ate__isnull=True)
        self.assertEqual(vigencia.autor, self.bruna)
        confere(self, resposta.json(), "publicar_200")

    def test_publicar_sem_mudanca_ainda_registra_o_clique(self):
        antes = PublicacaoCatalogo.objects.count()
        resposta = self.publica({"valores": [], "produtos": []})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["publicacao"]["alteracoes"], [])
        self.assertEqual(PublicacaoCatalogo.objects.count(), antes + 1)

    def test_mudanca_so_de_texto(self):
        resposta = self.publica({"produtos": [{"categoria": "elite", "slug": "evo", "nome": "ELITE EVO+", "sigla": "EVP"}]})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(
            resposta.json()["publicacao"]["alteracoes"],
            ['ELITE EVO: nome "ELITE EVO" → "ELITE EVO+"', 'ELITE EVO: sigla "EVO" → "EVP"'],
        )

    def test_muda_a_vigencia_e_o_total_acompanha(self):
        resposta = self.publica({"valores": [{"categoria": "elite", "produto": "pro", "monthly": 1000, "vigencia": 24}]})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        pro = Produto.objects.get(categoria__slug="elite", slug="pro")
        self.assertEqual((pro.vigencia_meses, pro.preco), (24, Decimal("24000.00")))
        self.assertIn("ELITE PRO: vigência 12 meses → 24 meses", resposta.json()["publicacao"]["alteracoes"])

    def test_valor_de_referencia_da_apn_se_limpa_com_null(self):
        self.publica({"valores": [{"categoria": "apn", "valor_referencia": 35000}]})
        resposta = self.publica({"valores": [{"categoria": "apn", "valor_referencia": None}]})
        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(Categoria.objects.get(slug="apn").valor_referencia)
        self.assertEqual(resposta.json()["publicacao"]["alteracoes"], ["APN: valor de referência R$ 35.000,00 → —"])

    def test_gerente_recebe_403_e_nada_muda(self):
        antes = PublicacaoCatalogo.objects.count()
        resposta = self.publica(self.CORPO, sub=RAFAEL)
        self.assertRecusa(resposta, 403, "SEM_PERMISSAO")
        self.assertEqual(PublicacaoCatalogo.objects.count(), antes)
        self.assertNotEqual(Produto.objects.get(categoria__slug="elite", slug="pro").mensalidade, Decimal("10497.00"))

    def test_impersonacao_nao_publica(self):
        self.assertRecusa(self.publica(self.CORPO, imp=True, act=9), 403, "IMPERSONACAO_RECUSADA")


class PublicarTudoOuNadaTest(Base):
    def assertNadaMudou(self, resposta, codigo):
        self.assertRecusa(resposta, 400, codigo)
        self.assertEqual(PublicacaoCatalogo.objects.count(), self.publicacoes)
        pro = Produto.objects.get(categoria__slug="elite", slug="pro")
        self.assertEqual((pro.mensalidade, pro.nome), self.pro)

    def setUp(self):
        super().setUp()
        self.publicacoes = PublicacaoCatalogo.objects.count()
        pro = Produto.objects.get(categoria__slug="elite", slug="pro")
        self.pro = (pro.mensalidade, pro.nome)
        self.valida = {"categoria": "elite", "produto": "pro", "monthly": 1}

    def test_produto_desconhecido_recusa_a_publicacao_inteira(self):
        resposta = self.publica({"valores": [self.valida, {"categoria": "elite", "produto": "nao-existe", "monthly": 2}]})
        self.assertNadaMudou(resposta, "PRODUTO_DESCONHECIDO")
        confere(self, resposta.json(), "publicar_400_produto_desconhecido")

    def test_produto_desconhecido_na_lista_de_textos(self):
        resposta = self.publica({"valores": [self.valida], "produtos": [{"categoria": "apn", "slug": "pro", "nome": "X"}]})
        self.assertNadaMudou(resposta, "PRODUTO_DESCONHECIDO")

    def test_categoria_desconhecida(self):
        resposta = self.publica({"valores": [self.valida, {"categoria": "xpto", "valor_referencia": 1}]})
        self.assertNadaMudou(resposta, "CATEGORIA_DESCONHECIDA")

    def test_campo_desconhecido_e_recusado_e_nao_ignorado(self):
        resposta = self.publica({"valores": [{**self.valida, "mensalidade": 2}]})
        self.assertNadaMudou(resposta, "CAMPO_DESCONHECIDO")
        resposta = self.publica({"valores": [self.valida], "extra": []})
        self.assertNadaMudou(resposta, "CAMPO_DESCONHECIDO")

    def test_preco_em_produto_de_formulario(self):
        resposta = self.publica({"valores": [self.valida, {"categoria": "produtos", "produto": "recrutamento-selecao", "price": 1}]})
        self.assertNadaMudou(resposta, "PRODUTO_SEM_PRECO")

    def test_recorrente_com_price_e_avulso_com_monthly(self):
        self.assertNadaMudou(self.publica({"valores": [{"categoria": "elite", "produto": "pro", "price": 1}]}), "DADOS_INVALIDOS")
        self.assertNadaMudou(
            self.publica({"valores": [self.valida, {"categoria": "elite", "produto": "imersao", "monthly": 1}]}),
            "DADOS_INVALIDOS",
        )

    def test_valores_fora_da_faixa(self):
        for monthly in (0, -10, 10**9, "abc", None, True):
            with self.subTest(monthly=monthly):
                resposta = self.publica({"valores": [{"categoria": "elite", "produto": "pro", "monthly": monthly}]})
                self.assertNadaMudou(resposta, "DADOS_INVALIDOS")

    def test_valor_de_referencia_em_categoria_comum(self):
        resposta = self.publica({"valores": [self.valida, {"categoria": "elite", "valor_referencia": 10}]})
        self.assertNadaMudou(resposta, "DADOS_INVALIDOS")

    def test_sigla_que_ja_e_de_outro_produto(self):
        resposta = self.publica({"valores": [self.valida], "produtos": [{"categoria": "elite", "slug": "pro", "sigla": "EVO"}]})
        self.assertNadaMudou(resposta, "DADOS_INVALIDOS")
        self.assertIn("EVO", resposta.json()["erro"])

    def test_produto_repetido(self):
        resposta = self.publica({"valores": [self.valida, self.valida]})
        self.assertNadaMudou(resposta, "DADOS_INVALIDOS")

    def test_corpo_que_nao_e_objeto(self):
        self.assertNadaMudou(self.publica([1, 2]), "DADOS_INVALIDOS")
        self.assertNadaMudou(self.publica({"valores": {"a": 1}}), "DADOS_INVALIDOS")

    def test_nome_vazio(self):
        resposta = self.publica({"valores": [self.valida], "produtos": [{"categoria": "elite", "slug": "pro", "nome": "  "}]})
        self.assertNadaMudou(resposta, "DADOS_INVALIDOS")


class CategoriaTest(Base):
    def test_libera_e_tira_com_autor_e_historico(self):
        resposta = self.como("patch", "/api/integracao/categorias/treinamentos", {"locked": False})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        confere(self, resposta.json(), "categoria_200")
        self.assertFalse(Categoria.objects.get(slug="treinamentos").travada)
        treinamentos = next(c for c in APIClient().get("/api/catalogo").json()["cats"] if c["id"] == "treinamentos")
        self.assertNotIn("locked", treinamentos)

        vigencia = Vigencia.objects.get(chave="categoria:treinamentos", campo="travada", vigente_ate__isnull=True)
        self.assertEqual((vigencia.valor, vigencia.autor), ("não", self.bruna))
        semente = Vigencia.objects.get(chave="categoria:treinamentos", campo="travada", vigente_ate__isnull=False)
        self.assertEqual((semente.valor, semente.encerrado_por), ("sim", self.bruna))

        resposta = self.como("patch", "/api/integracao/categorias/treinamentos", {"locked": True})
        self.assertEqual(resposta.json()["alteracao"], "Treinamentos: tirada do Lobby (em implementação)")
        self.assertTrue(Categoria.objects.get(slug="treinamentos").travada)

    def test_mesmo_estado_nao_grava_nada(self):
        antes = Vigencia.objects.count()
        resposta = self.como("patch", "/api/integracao/categorias/elite", {"locked": False})
        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(resposta.json()["alteracao"])
        self.assertEqual(Vigencia.objects.count(), antes)

    def test_corpo_invalido(self):
        for corpo in ({}, {"locked": "true"}, {"locked": 1}, {"locked": True, "nome": "X"}, [True]):
            with self.subTest(corpo=corpo):
                self.assertRecusa(self.como("patch", "/api/integracao/categorias/elite", corpo), 400, "DADOS_INVALIDOS")
        self.assertFalse(Categoria.objects.get(slug="elite").travada)

    def test_categoria_desconhecida(self):
        self.assertRecusa(self.como("patch", "/api/integracao/categorias/xpto", {"locked": True}), 404, "NAO_ENCONTRADA")

    def test_gerente_nao_mexe(self):
        resposta = self.como("patch", "/api/integracao/categorias/elite", {"locked": True}, sub=RAFAEL)
        self.assertRecusa(resposta, 403, "SEM_PERMISSAO")
        self.assertFalse(Categoria.objects.get(slug="elite").travada)


class HistoricoTest(Base):
    GERAL = {"dia_vencimento": 15, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}

    def setUp(self):
        super().setUp()
        sincronizar([dep("1001", "Consultoria", "001.001"), dep("1002", "Comercial", "001.002")])

    def historico(self, **filtros):
        consulta = "&".join(f"{k}={v}" for k, v in filtros.items())
        return self.como("get", f"/api/integracao/historico?{consulta}")

    def test_cobranca_da_categoria_com_de_para(self):
        self.como("put", "/api/cobranca", {"categorias": {"elite": {**self.GERAL, "dia_vencimento": 10}}})
        resposta = self.historico(area="cobranca", categoria="elite")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        confere(self, resposta.json(), "historico_cobranca_elite")

    def test_dia_fixo_desligado_com_de_para(self):
        """TSK-1077. A semente da migration não aparece: o evento é só a mudança."""
        self.como("put", "/api/cobranca", {"categorias": {"elite": {**self.GERAL, "dia_fixo": False}}})
        resposta = self.historico(area="cobranca", categoria="elite")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        confere(self, resposta.json(), "historico_cobranca_elite_dia_fixo")

    def test_remocao_de_excecao_aparece_com_quem_removeu(self):
        excecao = {"categoria_id": "elite", "produto_id": "pro", **self.GERAL, "dia_vencimento": 5}
        self.como("put", "/api/cobranca", {"categorias": {}, "excecoes": [excecao]})
        self.como("put", "/api/cobranca", {"categorias": {}, "excecoes": []}, sub=BRUNA)
        itens = self.historico(area="cobranca", produto="pro").json()["itens"]
        self.assertEqual(itens[0]["titulo"], "Exceção de cobrança removida · ELITE PRO")
        self.assertEqual(itens[0]["autor"], BRUNA)
        self.assertEqual(itens[1]["titulo"], "Exceção de cobrança · ELITE PRO")

    def test_junta_as_areas_do_mais_recente_para_o_mais_antigo(self):
        self.publica({"valores": [{"categoria": "elite", "produto": "pro", "monthly": 1000}]})
        self.como("put", "/api/departamentos/configuracao", {"categorias": {"elite": {"modo": "fixo", "codigo": "1002"}}})
        self.como("put", "/api/categorias-financeiras/configuracao", {"modo": "lista"})
        self.como("put", "/api/dh-avaliacao/valores/configuracao", {
            "modalidades": {"relatorio": {"valor": 180, "fixo": True}, "devolutiva": {"valor": 450, "fixo": False}}
        })
        self.como("patch", "/api/integracao/categorias/treinamentos", {"locked": False})

        corpo = self.historico(autor=BRUNA).json()
        areas = [item["area"] for item in corpo["itens"]]
        self.assertEqual(areas, ["categorias", "dh_avaliacao", "categoria_financeira", "departamento", "catalogo"])
        quandos = [item["quando"] for item in corpo["itens"]]
        self.assertEqual(quandos, sorted(quandos, reverse=True))
        self.assertTrue(all(item["autor"] == BRUNA for item in corpo["itens"]))

        departamento = next(i for i in corpo["itens"] if i["area"] == "departamento")
        self.assertEqual(departamento["alteracoes"], ["Elite: lista completa → fixo: Comercial"])
        self.assertEqual(departamento["categoria"], "elite")

    def test_filtro_por_produto_acha_a_publicacao(self):
        self.publica({"valores": [{"categoria": "elite", "produto": "pro", "monthly": 1000}]})
        self.publica({"valores": [{"categoria": "elite", "produto": "evo", "monthly": 2000}]})
        itens = self.historico(area="catalogo", produto="pro", autor=BRUNA).json()["itens"]
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["produto"], "pro")
        self.assertTrue(itens[0]["alteracoes"][0].startswith("ELITE PRO: mensalidade "))

    def test_preco_mudado_pelo_patch_de_antes_aparece_sem_duplicar(self):
        """Vigência de preço sem publicação (o PATCH de antes da TSK-1059) entra
        no histórico; com publicação, só a publicação aparece."""
        produto = Produto.objects.get(categoria__slug="elite", slug="gestao")
        from apps.catalogo import vigencias

        vigencias.registrar("gestao", produto.nome, "mensalidade", Decimal("1.00"), autor=self.bruna)
        self.publica({"valores": [{"categoria": "elite", "produto": "pro", "monthly": 1000}]})
        itens = self.historico(area="catalogo", autor=BRUNA).json()["itens"]
        titulos = [i["titulo"] for i in itens]
        self.assertEqual(titulos.count("Catálogo publicado"), 1)
        self.assertIn("Preço · ELITE GESTÃO", titulos)
        self.assertNotIn("Preço · ELITE PRO", titulos)

    def test_pagina(self):
        for valor in (1000, 1001, 1002, 1003, 1004):
            self.publica({"valores": [{"categoria": "elite", "produto": "pro", "monthly": valor}]})
        primeira = self.historico(area="catalogo", autor=BRUNA, por_pagina=2).json()
        self.assertEqual((primeira["total"], primeira["paginas"], len(primeira["itens"])), (5, 3, 2))
        terceira = self.historico(area="catalogo", autor=BRUNA, por_pagina=2, pagina=3).json()
        self.assertEqual(len(terceira["itens"]), 1)
        self.assertIn("R$ 1.000,00", terceira["itens"][0]["alteracoes"][0])

    def test_area_desconhecida(self):
        self.assertRecusa(self.historico(area="xpto"), 400, "DADOS_INVALIDOS")

    def test_so_diretoria(self):
        self.assertRecusa(self.como("get", "/api/integracao/historico", sub=RAFAEL), 403, "SEM_PERMISSAO")

    def test_semente_da_trava_e_situacao_inicial(self):
        itens = self.historico(area="categorias", categoria="treinamentos").json()["itens"]
        self.assertEqual(itens[-1]["titulo"], "Treinamentos: situação inicial")
        self.assertEqual(itens[-1]["alteracoes"], ["situação: fora do Lobby (em implementação)"])
        self.assertIsNone(itens[-1]["autor"])


class ContratoDasConfiguracoesTest(Base):
    def test_cobranca_por_categoria(self):
        confere(self, self.como("get", "/api/cobranca").json(), "cobranca")

    def test_cobranca_com_o_dia_fixo_desligado(self):
        """TSK-1077: `dia_fixo` em cada regra de `categorias.<slug>` e em cada item
        de `excecoes` — o que o Admin Lobby do Conecta grava e lê (TSK-1078)."""
        regra = {"dia_vencimento": 15, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}
        resposta = self.como("put", "/api/cobranca", {
            "categorias": {"elite": {**regra, "dia_fixo": False}},
            "excecoes": [
                {"categoria_id": "elite", "produto_id": "pro", **regra, "dia_vencimento": 5, "dia_fixo": False},
                {"categoria_id": "elite", "produto_id": "evo", **regra, "dia_vencimento": 10},
            ],
        })
        self.assertEqual(resposta.status_code, 200, resposta.content)
        confere(self, self.como("get", "/api/cobranca").json(), "cobranca_dia_fixo_desligado")

    def test_departamentos_por_categoria(self):
        sincronizar([dep("1001", "Consultoria", "001.001"), dep("1002", "Comercial", "001.002")])
        resposta = self.como(
            "put", "/api/departamentos/configuracao",
            {"categorias": {"elite": {"modo": "fixo", "codigo": "1002"}, "apn": {"modo": "lista"}, "produtos": {"modo": "lista"}}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        confere(self, resposta.json(), "departamentos_configuracao")


class AchadosDaRevisaoTest(Base):
    """O que a revisão de contexto limpo da TSK-1059 achou, um teste por achado."""

    def test_sigla_repetida_dentro_do_lote_e_400_e_nao_500(self):
        antes = PublicacaoCatalogo.objects.count()
        resposta = self.publica({"produtos": [
            {"categoria": "elite", "slug": "base", "sigla": "ZZZ"},
            {"categoria": "elite", "slug": "pre", "sigla": "ZZZ"},
        ]})
        self.assertRecusa(resposta, 400, "DADOS_INVALIDOS")
        self.assertIn("ZZZ", resposta.json()["erro"])
        # Sigla fora do formato recebe a mensagem do formato, não a de repetição.
        resposta = self.publica({"produtos": [
            {"categoria": "elite", "slug": "base", "sigla": "ab"},
            {"categoria": "elite", "slug": "pre", "sigla": "ab"},
        ]})
        self.assertIn("3 letras", resposta.json()["erro"])
        self.assertEqual(PublicacaoCatalogo.objects.count(), antes)
        self.assertFalse(Produto.objects.filter(sigla="ZZZ").exists())

    def test_nan_e_infinito_sao_recusados_sem_500(self):
        for valor in ("NaN", "nan", "Infinity", "-Infinity"):
            with self.subTest(valor=valor):
                resposta = self.publica({"valores": [{"categoria": "elite", "produto": "pro", "monthly": valor}]})
                self.assertRecusa(resposta, 400, "DADOS_INVALIDOS")
                resposta = self.publica({"valores": [{"categoria": "elite", "produto": "imersao", "price": valor}]})
                self.assertRecusa(resposta, 400, "DADOS_INVALIDOS")

    def test_nan_no_put_antigo_e_no_post_de_produto_tambem(self):
        cliente = APIClient()
        cliente.force_authenticate(user=self.bruna)
        cats = cliente.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        for valor in ("NaN", 1e26, 10**27, "1e1000000"):
            elite["products"][0]["monthly"] = valor
            with self.subTest(valor=str(valor)[:12]):
                self.assertEqual(cliente.put("/api/catalogo", {"cats": cats}, format="json").status_code, 400)
        resposta = cliente.post("/api/produtos", {"categoria_id": "elite", "nome": "X", "sigla": "XXX",
                                                  "recorrente": False, "valor": "NaN"}, format="json")
        self.assertEqual(resposta.status_code, 400)

    def test_post_de_produto_saiu_da_lista_e_o_admin_antigo_ainda_cria(self):
        """TSK-1077: produto novo entra por desenvolvimento, não pelo Conecta."""
        corpo = {
            "categoria_id": "elite", "nome": "ELITE MASTER", "sigla": "MAS",
            "recorrente": True, "mensalidade": "39997", "vigencia_meses": 12,
        }
        publicacoes = PublicacaoCatalogo.objects.count()
        resposta = self.como("post", "/api/produtos", corpo)
        self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")
        confere(self, resposta.json(), "erro_rota_nao_liberada")
        self.assertFalse(Produto.objects.filter(slug="elite-master").exists())
        self.assertEqual(PublicacaoCatalogo.objects.count(), publicacoes)

        # O /admin antigo, com o JWT de sessão de hoje, continua criando.
        from rest_framework_simplejwt.tokens import RefreshToken

        cliente = APIClient()
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.bruna).access_token}")
        resposta = cliente.post("/api/produtos", corpo, format="json")
        self.assertEqual(resposta.status_code, 201, resposta.content)
        publicacao = PublicacaoCatalogo.objects.order_by("-publicado_em", "-id").first()
        self.assertEqual(publicacao.autor, self.bruna)
        self.assertEqual(Vigencia.objects.get(chave="elite-master", campo="mensalidade").autor, self.bruna)

    def test_excecoes_null_no_corpo_novo_e_recusado(self):
        excecao = {"categoria_id": "elite", "produto_id": "pro", "dia_vencimento": 5,
                   "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}
        self.como("put", "/api/cobranca", {"categorias": {}, "excecoes": [excecao]})
        resposta = self.como("put", "/api/cobranca", {"categorias": {}, "excecoes": None})
        self.assertRecusa(resposta, 400, "DADOS_INVALIDOS")
        self.assertTrue(Produto.objects.get(categoria__slug="elite", slug="pro").politica_cobranca)


class HistoricoLegadoTest(Base):
    """Publicação de antes da TSK-1059: sem `alvos`, com a vigência do PUT antigo."""

    def setUp(self):
        super().setUp()
        from apps.catalogo import vigencias

        produto = Produto.objects.get(categoria__slug="elite", slug="pro")
        vigencias.registrar("pro", produto.nome, "mensalidade", Decimal("11500.00"), autor=self.bruna)
        PublicacaoCatalogo.objects.create(
            autor=self.bruna, autor_email=BRUNA, catalogo=[], resumo="ELITE PRO: 12997.00 → 11500.00", alvos=""
        )

    def historico(self, **filtros):
        consulta = "&".join(f"{k}={v}" for k, v in filtros.items())
        return self.como("get", f"/api/integracao/historico?{consulta}").json()["itens"]

    def test_filtro_por_produto_acha_a_publicacao_antiga(self):
        itens = self.historico(area="catalogo", produto="pro", autor=BRUNA)
        self.assertEqual([i["titulo"] for i in itens], ["Catálogo publicado"])
        self.assertEqual(itens[0]["alteracoes"], ["ELITE PRO: 12997.00 → 11500.00"])
        self.assertEqual((itens[0]["categoria"], itens[0]["produto"]), ("elite", "pro"))

    def test_filtro_por_categoria_acha_a_publicacao_antiga_e_a_excecao_de_produto(self):
        excecao = {"categoria_id": "elite", "produto_id": "pro", "dia_vencimento": 5,
                   "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}
        self.como("put", "/api/cobranca", {"categorias": {}, "excecoes": [excecao]})
        itens = self.historico(categoria="elite", autor=BRUNA)
        titulos = [i["titulo"] for i in itens]
        self.assertIn("Catálogo publicado", titulos)
        self.assertIn("Exceção de cobrança · ELITE PRO", titulos)
        self.assertEqual(self.historico(categoria="apn", autor=BRUNA), [])


class TravaPeloDjangoAdminTest(Base):
    def test_mudar_a_trava_no_django_admin_entra_na_linha_do_tempo(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        categoria = Categoria.objects.get(slug="treinamentos")
        categoria.travada = False
        pedido = RequestFactory().post("/")
        pedido.user = self.bruna
        formulario = type("Form", (), {"changed_data": ["travada"]})()
        django_admin.site._registry[Categoria].save_model(pedido, categoria, formulario, True)

        aberta = Vigencia.objects.get(chave="categoria:treinamentos", campo="travada", vigente_ate__isnull=True)
        self.assertEqual((aberta.valor, aberta.autor), ("não", self.bruna))
        # E a volta pelo Conecta, para o estado da semente, é registrada.
        resposta = self.como("patch", "/api/integracao/categorias/treinamentos", {"locked": True})
        self.assertEqual(resposta.status_code, 200)
        aberta = Vigencia.objects.get(chave="categoria:treinamentos", campo="travada", vigente_ate__isnull=True)
        self.assertEqual(aberta.valor, "sim")


class AchadosDoCodexTest(Base):
    def test_patch_antigo_seguido_de_put_de_outro_valor_aparece_inteiro(self):
        """PATCH 100→200 (sem publicação, antes da TSK) e logo depois PUT 200→300:
        as duas mudanças aparecem, e a publicação não esconde a do PATCH."""
        from apps.catalogo import vigencias

        produto = Produto.objects.get(categoria__slug="elite", slug="pro")
        vigencias.registrar("pro", produto.nome, "mensalidade", Decimal("200.00"), autor=self.bruna)
        vigencias.registrar("pro", produto.nome, "mensalidade", Decimal("300.00"), autor=self.bruna)
        PublicacaoCatalogo.objects.create(
            autor=self.bruna, autor_email=BRUNA, catalogo=[], resumo="ELITE PRO: 200.00 → 300.00", alvos=""
        )
        itens = self.como("get", f"/api/integracao/historico?area=catalogo&produto=pro&autor={BRUNA}").json()["itens"]
        titulos = [i["titulo"] for i in itens]
        self.assertEqual(titulos.count("Catálogo publicado"), 1)
        preco = [i for i in itens if i["titulo"] == "Preço · ELITE PRO"]
        self.assertEqual(len(preco), 1)
        self.assertTrue(preco[0]["alteracoes"][0].endswith("→ R$ 200,00"))

    def test_cobranca_aplica_as_categorias_na_ordem_do_id(self):
        from unittest import mock

        from apps.catalogo import cobranca

        regra = {"dia_vencimento": 10, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 0}
        ordem = []
        original = cobranca._grava_da_categoria
        with mock.patch.object(cobranca, "_grava_da_categoria", side_effect=lambda c, *a, **k: (ordem.append(c.pk), original(c, *a, **k))):
            resposta = self.como("put", "/api/cobranca", {"categorias": {"produtos": regra, "apn": regra, "elite": regra}})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(ordem, sorted(ordem))


class VersaoDosDepartamentosTest(Base):
    def test_renomear_departamento_com_categorias_divergentes_muda_a_versao(self):
        sincronizar([dep("1001", "Consultoria", "001.001"), dep("1002", "Comercial", "001.002")])
        self.como("put", "/api/departamentos/configuracao", {"categorias": {"elite": {"modo": "fixo", "codigo": "1002"}}})
        antes = APIClient().get("/api/departamentos").json()["versao"]
        sincronizar([dep("1001", "Consultoria Elite", "001.001"), dep("1002", "Comercial", "001.002")])
        depois = APIClient().get("/api/departamentos").json()
        self.assertNotEqual(depois["versao"], antes)
        self.assertEqual(depois["categorias"]["apn"]["departamentos"][0]["descricao"], "Consultoria Elite")
