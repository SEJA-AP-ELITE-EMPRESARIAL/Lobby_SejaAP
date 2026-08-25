"""
Produto novo no catálogo — a operação que a Elite está a ponto de fazer.

CONTEXTO

A Elite vai receber dois planos além dos quatro que estão no ar. Isso não é
mudança de código: produto é linha no banco, criada no /django-admin/. Este
arquivo é o que garante que continue sendo — que criar a linha baste para o
consultor ver o produto, cotar e vender.

O que cada teste aqui protege, em uma frase: o JSON de um produto novo tem que
sair no MESMO formato que o front já sabe ler, e as siglas não podem colidir.

Por que a sigla merece cinco testes: ela é o `SSS` do protocolo da venda
(`SSS-YYMMDDPRRRRR`), o único pedaço do protocolo que diz o que foi vendido.
Sigla repetida não quebra nada na hora — quebra a conciliação, meses depois.

O que NÃO é testado aqui, e onde está: o cronograma de pagamento (entrada + uma
parcela por mês, todas no dia 15) é calculado no navegador, em `index.html`
(`parcelaVencimento`, `initPayment`). O que este arquivo garante é o insumo dele:
que `vigencia` chega ao front, porque é dela que sai o número de parcelas de um
plano com vigência diferente de 12 meses.
"""
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.contas.models import Papel

from .models import Categoria, Produto
from .tests_escrita import cria_pessoa


def cria_produto(**campos):
    """Cria um produto como o /django-admin/ cria: passando por full_clean().

    Isto importa: `clean()` é onde vive a conferência de sigla contra as
    categorias, e um teste que chame `objects.create()` direto não a exercita —
    passaria mesmo se a validação não existisse.
    """
    produto = Produto(**campos)
    produto.full_clean()
    produto.save()
    return produto


class ProdutoNovoNaEliteTest(TestCase):
    """Dois planos novos na Elite, do jeito que serão criados de verdade."""

    def setUp(self):
        self.client = APIClient()
        self.elite = Categoria.objects.get(slug="elite")
        self.proxima_ordem = (
            self.elite.produtos.count()
        )  # os quatro atuais ocupam 0..3

    def _elite_servida(self):
        cats = self.client.get("/api/catalogo").json()["cats"]
        return next(c for c in cats if c["id"] == "elite")

    def test_dois_planos_novos_entram_no_json_no_formato_de_sempre(self):
        cria_produto(
            categoria=self.elite,
            slug="master",
            nome="ELITE MASTER",
            sigla="MAS",
            descricao="O plano do Domínio",
            duracao="12 meses",
            icone="shield",
            recorrente=True,
            mensalidade=Decimal("39997"),
            vigencia_meses=12,
            ordem=self.proxima_ordem,
        )
        cria_produto(
            categoria=self.elite,
            slug="black",
            nome="ELITE BLACK",
            sigla="BLK",
            descricao="O plano do Topo",
            duracao="12 meses",
            icone="stars",
            recorrente=True,
            mensalidade=Decimal("49997"),
            vigencia_meses=12,
            ordem=self.proxima_ordem + 1,
        )

        produtos = self._elite_servida()["products"]
        # Entram no FIM, na ordem do campo `ordem` — os planos que já existiam não
        # se movem. A lista é conferida por id, e não por posição, para o teste não
        # quebrar toda vez que a Elite ganhar um plano.
        self.assertEqual(
            [p["id"] for p in produtos][-2:], ["master", "black"]
        )
        self.assertEqual(len(produtos), self.proxima_ordem + 2)
        self.assertEqual(
            next(p for p in produtos if p["id"] == "master"),
            {
                "id": "master",
                "name": "ELITE MASTER",
                "sigla": "MAS",
                "desc": "O plano do Domínio",
                "duration": "12 meses",
                "icon": "shield",
                "price": 39997 * 12,
                "monthly": 39997,
                "recurring": True,
                "vigencia": 12,
            },
        )

    def test_vigencia_diferente_de_12_chega_ao_front(self):
        """É de `vigencia` que o front tira o nº de parcelas do cronograma.

        Um plano de 24 meses vira entrada + 23 parcelas (`initPayment`,
        `index.html`). Se a vigência não viesse no JSON, o front cairia no 12
        padrão e cada parcela sairia pelo dobro da mensalidade.
        """
        cria_produto(
            categoria=self.elite,
            slug="bienal",
            nome="ELITE BIENAL",
            sigla="BIE",
            descricao="Dois anos de acompanhamento",
            duracao="24 meses",
            icone="event_repeat",
            recorrente=True,
            mensalidade=Decimal("15000"),
            vigencia_meses=24,
            ordem=self.proxima_ordem,
        )

        novo = next(p for p in self._elite_servida()["products"] if p["id"] == "bienal")
        self.assertEqual(novo["vigencia"], 24)
        self.assertEqual(novo["monthly"], 15000)
        self.assertEqual(novo["price"], 15000 * 24)
        self.assertIsInstance(novo["price"], int)

    def test_plano_avulso_na_elite_nao_emite_campos_de_recorrencia(self):
        """A Elite pode receber produto de valor único (uma imersão fechada).

        `recurring` ausente é COMO o front distingue os dois: presente, ele lê
        mensalidade × vigência; ausente, valor à vista.
        """
        cria_produto(
            categoria=self.elite,
            slug="intensivo",
            nome="ELITE INTENSIVO",
            sigla="INT",
            descricao="Encontro fechado de 2 dias",
            duracao="2 dias",
            icone="groups",
            recorrente=False,
            valor=Decimal("28000"),
            ordem=self.proxima_ordem,
        )

        novo = next(
            p for p in self._elite_servida()["products"] if p["id"] == "intensivo"
        )
        self.assertEqual(novo["price"], 28000)
        for campo in ("monthly", "recurring", "vigencia"):
            self.assertNotIn(campo, novo)

    def test_ordem_decide_a_posicao_na_tela(self):
        """Plano novo pode nascer no meio da lista, sem renomear nada."""
        cria_produto(
            categoria=self.elite,
            slug="plus",
            nome="ELITE PRO PLUS",
            sigla="PLU",
            descricao="Entre o PRO e o GESTÃO",
            duracao="12 meses",
            icone="workspace_premium",
            recorrente=True,
            mensalidade=Decimal("16000"),
            vigencia_meses=12,
            ordem=1,  # mesma ordem do PRÉ; o desempate é o id
        )

        # Ordem 1 é a mesma do ELITE PRÉ; o desempate é o id, e o PRÉ é mais
        # antigo. O plano novo entra logo depois dele.
        ids = [p["id"] for p in self._elite_servida()["products"]]
        self.assertEqual(ids[:4], ["base", "pre", "plus", "pro"])


class SiglaDoProdutoNovoTest(TestCase):
    """A sigla é única no catálogo inteiro — produtos e categorias juntos."""

    def setUp(self):
        self.elite = Categoria.objects.get(slug="elite")

    def _campos(self, **sobrescreve):
        base = dict(
            categoria=self.elite,
            slug="novo",
            nome="ELITE NOVO",
            sigla="NOV",
            descricao="Plano novo",
            duracao="12 meses",
            icone="diamond",
            recorrente=True,
            mensalidade=Decimal("10000"),
            vigencia_meses=12,
            ordem=9,
        )
        base.update(sobrescreve)
        return base

    def test_sigla_de_outro_produto_e_recusada_no_formulario(self):
        with self.assertRaises(ValidationError) as erro:
            cria_produto(**self._campos(sigla="PRO"))
        self.assertIn("sigla", erro.exception.message_dict)

    def test_sigla_de_outro_produto_e_recusada_tambem_no_banco(self):
        """A validação de formulário não alcança shell nem script.

        Quem cria produto por `manage.py shell` passa por fora do `full_clean()`.
        Por isso a garantia final é o índice único, não a mensagem bonita.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Produto.objects.create(**self._campos(sigla="PRO"))

    def test_sigla_de_categoria_de_fluxo_proprio_e_recusada(self):
        """A APN não tem produto: a sigla dela mesma vai ao protocolo.

        Um produto com sigla APN geraria protocolos indistinguíveis dos da
        categoria APN — e é o índice único que NÃO pega este caso, porque são
        tabelas diferentes.
        """
        with self.assertRaises(ValidationError) as erro:
            cria_produto(**self._campos(sigla="APN"))
        self.assertIn("APN", str(erro.exception))

    def test_categoria_nao_pode_tomar_a_sigla_de_um_produto(self):
        """O outro lado da mesma regra."""
        categoria = Categoria(
            slug="mentorias",
            nome="Mentorias",
            icone="school",
            cor="gold",
            fluxo="mentoria",
            sigla="EVO",  # já é do ELITE EVO
            ordem=9,
        )
        with self.assertRaises(ValidationError) as erro:
            categoria.full_clean()
        self.assertIn("sigla", erro.exception.message_dict)

    def test_sigla_em_minuscula_e_normalizada(self):
        """Digitar `mas` é erro de digitação, não de conteúdo."""
        produto = cria_produto(**self._campos(sigla="mas"))
        self.assertEqual(produto.sigla, "MAS")
        self.assertEqual(Produto.objects.get(pk=produto.pk).sigla, "MAS")

    def test_sigla_normalizada_ainda_colide(self):
        """Normalizar não pode ser porta dos fundos para repetir sigla."""
        with self.assertRaises(ValidationError):
            cria_produto(**self._campos(sigla="pro"))

    def test_sigla_fora_do_formato_e_recusada(self):
        for invalida in ("PR", "PROX", "PR1", "P-O", ""):
            with self.subTest(sigla=invalida):
                with self.assertRaises(ValidationError):
                    cria_produto(**self._campos(sigla=invalida))

    def test_slug_repetido_na_mesma_categoria_e_recusado(self):
        """O id do produto vai no payload da venda; repetido, some no `find`."""
        with self.assertRaises(ValidationError):
            cria_produto(**self._campos(slug="pro", sigla="NOV"))


class ProdutoDeFormularioTest(TestCase):
    """As invariantes do produto de fluxo próprio, no nível do banco.

    A semente já traz um (o Recrutamento e Seleção); o que se testa aqui é o que
    o banco recusa em volta dele.
    """

    def setUp(self):
        self.produto = Produto.objects.get(slug="recrutamento-selecao")

    def test_nasce_sem_preco_nenhum(self):
        self.assertEqual(self.produto.fluxo, "dh")
        self.assertTrue(self.produto.de_formulario)
        self.assertFalse(self.produto.recorrente)
        self.assertIsNone(self.produto.preco)

    def test_o_banco_recusa_preco_num_produto_de_formulario(self):
        """A constraint, não a validação de formulário: shell e script passam por fora."""
        self.produto.valor = Decimal("9900")
        with self.assertRaises(IntegrityError):
            self.produto.save()

    def test_o_formulario_explica_antes_de_o_banco_recusar(self):
        """`full_clean` diz o que fazer; o IntegrityError não diria nada."""
        self.produto.recorrente = True
        self.produto.mensalidade = Decimal("900")
        self.produto.vigencia_meses = 12
        with self.assertRaises(ValidationError) as erro:
            self.produto.full_clean()
        self.assertIn("não tem preço", str(erro.exception))

    def test_fluxo_desconhecido_e_recusado(self):
        """Cada fluxo é um formulário no `index.html`. Um valor que o front não
        conhece põe no lobby um produto que não abre."""
        self.produto.fluxo = "recrutamento"
        with self.assertRaises(ValidationError):
            self.produto.full_clean()


class FallbackDoFrontTest(TestCase):
    """`exportar_cats_do_front` — o último passo manual de criar um produto.

    O `index.html` guarda uma cópia do catálogo para quando a API não responde.
    Criar produto no banco não a atualiza, e transcrevê-la à mão é onde se erra
    um dígito da mensalidade. O comando gera o bloco; estes testes garantem que
    ele sai no formato que o arquivo espera.
    """

    def setUp(self):
        cria_produto(
            categoria=Categoria.objects.get(slug="elite"),
            slug="master",
            nome="ELITE MASTER",
            sigla="MAS",
            descricao="O plano do Domínio",
            duracao="12 meses",
            icone="shield",
            recorrente=True,
            mensalidade=Decimal("39997"),
            vigencia_meses=12,
            ordem=4,
        )
        saida = StringIO()
        call_command("exportar_cats_do_front", stdout=saida, stderr=StringIO())
        self.bloco = saida.getvalue()

    def test_sai_como_o_array_do_arquivo(self):
        self.assertTrue(self.bloco.startswith("let CATS = ["))
        self.assertTrue(self.bloco.rstrip().endswith("];"))
        for slug in ("elite", "treinamentos", "apn", "produtos"):
            self.assertIn(f"{{ id: '{slug}',", self.bloco)

    def test_produto_de_formulario_sai_com_flow_e_sem_price(self):
        """A outra golden line: o Recrutamento e Seleção, como se cola no arquivo.

        `flow` presente e `price` ausente é o contrato inteiro do produto de
        fluxo próprio. Um `price: 0` que escapasse para cá viraria "R$ 0,00" na
        lista do consultor no dia em que a API caísse — que é justamente o dia
        em que ninguém vai conferir.
        """
        self.assertIn(
            "{ id: 'recrutamento-selecao', name: 'Recrutamento e Seleção', "
            "sigla: 'RES', desc: 'Processo seletivo conduzido pela Seja AP, vaga "
            "a vaga', duration: 'por vaga', flow: 'dh', icon: 'person_search' },",
            self.bloco,
        )

    def test_produto_novo_sai_na_mesma_ordem_de_campos_do_arquivo(self):
        """Golden line: é isto que se cola no index.html.

        A ordem dos campos é a do arquivo de hoje, de propósito — o diff do
        deploy tem que mostrar as linhas que mudaram de valor, não as que só
        mudaram de lugar.
        """
        self.assertIn(
            "{ id: 'master', name: 'ELITE MASTER', sigla: 'MAS', desc: 'O plano do Domínio', "
            "duration: '12 meses', monthly: 39997, price: 479964, recurring: true, "
            "vigencia: 12, icon: 'shield' },",
            self.bloco,
        )

    def test_apn_sai_sem_bloco_de_produtos_vazio(self):
        """Categoria de fluxo próprio não tem produto e nunca vai ter."""
        self.assertIn("flow: 'apn', sigla: 'APN',", self.bloco)
        self.assertIn("products: [] },", self.bloco)

    def test_acentos_sobrevivem(self):
        """Fallback com "PREPARA??O" é o que o consultor lê quando a API cai."""
        self.assertIn("ELITE PRÉ", self.bloco)
        self.assertIn("ELITE GESTÃO", self.bloco)
        self.assertIn("O plano da Fundação", self.bloco)

    def test_excecao_de_cobranca_sai_como_objeto_js(self):
        """Sem isto o `str(dict)` do Python vira uma STRING no arquivo.

        O front não quebraria — `normalizaCobranca` não entende a string e cai na
        política geral — mas o fallback passaria a cobrar na data errada, em
        silêncio, que é o pior desfecho possível para uma rede de segurança.
        """
        from .models import PoliticaCobranca

        PoliticaCobranca.objects.create(
            geral=False,
            produto=Produto.objects.get(slug="evo"),
            dia_vencimento=5,
            primeiro_vencimento="proximo",
            entrada_prazo_dias=3,
        )
        saida = StringIO()
        call_command("exportar_cats_do_front", stdout=saida, stderr=StringIO())
        bloco = saida.getvalue()

        self.assertIn(
            "cobranca: { dia_vencimento: 5, primeiro_vencimento: 'proximo', "
            "entrada_prazo_dias: 3 }",
            bloco,
        )
        self.assertNotIn("'{", bloco)  # nenhum dicionário virou texto


class PublicarPrecoDeProdutoNovoTest(TestCase):
    """Depois de criado, o plano novo tem que entrar no fluxo de preço da diretoria."""

    def setUp(self):
        self.client = APIClient()
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        cria_produto(
            categoria=Categoria.objects.get(slug="elite"),
            slug="master",
            nome="ELITE MASTER",
            sigla="MAS",
            descricao="O plano do Domínio",
            duracao="12 meses",
            icone="shield",
            recorrente=True,
            mensalidade=Decimal("39997"),
            vigencia_meses=12,
            ordem=4,
        )

    def test_diretoria_publica_o_preco_do_plano_novo(self):
        cats = self.client.get("/api/catalogo").json()["cats"]
        elite = next(c for c in cats if c["id"] == "elite")
        novo = next(p for p in elite["products"] if p["id"] == "master")
        novo["monthly"] = 42000

        cliente = APIClient()
        cliente.force_authenticate(user=self.diretoria)
        resposta = cliente.put("/api/catalogo", {"cats": cats}, format="json")

        self.assertEqual(resposta.status_code, 200, resposta.json())
        self.assertEqual(Produto.objects.get(slug="master").mensalidade, Decimal("42000.00"))
        # O total é derivado, nunca gravado: tem que acompanhar a mensalidade.
        publicado = next(
            p for p in next(c for c in resposta.json()["cats"] if c["id"] == "elite")["products"]
            if p["id"] == "master"
        )
        self.assertEqual(publicado["price"], 42000 * 12)
        self.assertEqual(resposta.json()["alteracoes"], ["ELITE MASTER: 39997.00 → 42000.00"])
