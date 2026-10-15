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

    def test_departamentos_por_categoria(self):
        sincronizar([dep("1001", "Consultoria", "001.001"), dep("1002", "Comercial", "001.002")])
        resposta = self.como(
            "put", "/api/departamentos/configuracao",
            {"categorias": {"elite": {"modo": "fixo", "codigo": "1002"}, "apn": {"modo": "lista"}, "produtos": {"modo": "lista"}}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        confere(self, resposta.json(), "departamentos_configuracao")
