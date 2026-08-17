"""
Publicação da tabela de preços: permissão, validação e histórico.

O corpo usado nos testes é o catálogo inteiro devolvido pelo próprio GET — é
exatamente assim que o `admin.html:222` publica, inclusive mandando de volta a
categoria APN que recebeu na leitura.
"""
from decimal import Decimal

import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.contas.models import Papel, VinculoIdentidade

from .models import Produto, PublicacaoCatalogo


def cria_avulso(slug="intensivo", valor=Decimal("28000")):
    """Um produto de valor à vista, para os testes que precisam de um.

    A semente não tem mais nenhum: os avulsos vinham de Treinamentos e Palestras,
    removidos em 17/08/2026 por nunca terem aberto para venda.
    """
    from .models import Categoria

    return Produto.objects.create(
        categoria=Categoria.objects.get(slug="elite"),
        slug=slug,
        nome="ELITE INTENSIVO",
        sigla="INT",
        descricao="Encontro fechado de 2 dias",
        duracao="2 dias",
        icone="groups",
        recorrente=False,
        valor=valor,
        ordem=99,
    )


def cria_pessoa(email, papel):
    """Usuário local + vínculo com papel, como nasceriam no primeiro login."""
    usuario = User.objects.create_user(username=email, email=email)
    usuario.set_unusable_password()
    usuario.save()
    VinculoIdentidade.objects.create(
        usuario=usuario, identidade_id=uuid.uuid4(), papel=papel
    )
    return usuario


class BaseEscrita(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.usuario = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)

    def catalogo_atual(self):
        return self.client.get("/api/catalogo").json()["cats"]

    def autenticado(self):
        cliente = APIClient()
        cliente.force_authenticate(user=self.usuario)
        return cliente

    def como(self, usuario):
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)
        return cliente

    @staticmethod
    def _acha(cats, cat_slug, prod_slug):
        categoria = next(c for c in cats if c["id"] == cat_slug)
        return next(p for p in categoria["products"] if p["id"] == prod_slug)


class PermissaoTest(BaseEscrita):
    def test_anonimo_nao_publica(self):
        resposta = self.client.put(
            "/api/catalogo", {"cats": self.catalogo_atual()}, format="json"
        )
        self.assertEqual(resposta.status_code, 401)

    def test_get_continua_anonimo_depois_de_fechar_a_escrita(self):
        """A trava é só na publicação. Cotar segue livre."""
        self.assertEqual(self.client.get("/api/catalogo").status_code, 200)

    def test_gerente_nao_publica_a_tabela(self):
        """A diferença entre os dois papéis é de ALCANCE.

        Gerente autoriza negociação numa venda; publicar a tabela muda o preço
        de todas as próximas, e isso é da diretoria.
        """
        gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        resposta = self.como(gerente).put(
            "/api/catalogo", {"cats": self.catalogo_atual()}, format="json"
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(Produto.objects.get(slug="pro").mensalidade, Decimal("12997.00"))

    def test_sem_papel_nao_publica(self):
        ninguem = cria_pessoa("novato@sejaap.com.br", "")
        resposta = self.como(ninguem).put(
            "/api/catalogo", {"cats": self.catalogo_atual()}, format="json"
        )
        self.assertEqual(resposta.status_code, 403)

    def test_diretoria_ganha_acesso_ao_django_admin(self):
        """`is_staff` acompanha o papel — senão a diretoria não alcança o
        /django-admin/, que é onde ficam as mudanças estruturais do catálogo."""
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.is_staff)

    def test_gerente_nao_e_staff(self):
        gerente = cria_pessoa("gerente2@sejaap.com.br", Papel.GERENTE)
        gerente.refresh_from_db()
        self.assertFalse(gerente.is_staff)

    def test_put_na_mesma_url_do_get(self):
        """O `admin.html` faz PUT em `/api/catalogo`, sem barra final.

        Com a rota registrada com barra, o APPEND_SLASH devolveria 301 e o corpo
        se perderia no redirecionamento.
        """
        resposta = self.autenticado().put(
            "/api/catalogo", {"cats": self.catalogo_atual()}, format="json"
        )
        self.assertEqual(resposta.status_code, 200)


class PublicacaoTest(BaseEscrita):
    def test_altera_mensalidade_e_recalcula_o_total(self):
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "pro")["monthly"] = 13997

        resposta = self.autenticado().put("/api/catalogo", {"cats": cats}, format="json")
        self.assertEqual(resposta.status_code, 200)

        pro = self._acha(resposta.json()["cats"], "elite", "pro")
        self.assertEqual(pro["monthly"], 13997)
        # O total é derivado, nunca aceito do corpo: 13997 × 12.
        self.assertEqual(pro["price"], 13997 * 12)

        self.assertEqual(
            Produto.objects.get(slug="pro").mensalidade, Decimal("13997.00")
        )

    def test_total_enviado_no_corpo_e_ignorado_em_recorrente(self):
        """O admin manda `price` junto, mas ele nunca é a fonte."""
        cats = self.catalogo_atual()
        produto = self._acha(cats, "elite", "pro")
        produto["monthly"] = 10000
        produto["price"] = 1  # valor absurdo, de propósito

        resposta = self.autenticado().put("/api/catalogo", {"cats": cats}, format="json")
        pro = self._acha(resposta.json()["cats"], "elite", "pro")
        self.assertEqual(pro["price"], 120000)

    def test_altera_valor_de_produto_avulso(self):
        """Produto avulso é criado aqui: desde 17/08/2026 a semente só tem
        recorrentes (os avulsos de Treinamentos e Palestras foram removidos)."""
        cria_avulso()
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "intensivo")["price"] = 19500

        resposta = self.autenticado().put("/api/catalogo", {"cats": cats}, format="json")
        avulso = self._acha(resposta.json()["cats"], "elite", "intensivo")
        self.assertEqual(avulso["price"], 19500)

    def test_apn_volta_na_resposta_mesmo_sendo_descartada_na_escrita(self):
        """Se sumisse, ela desapareceria da tela do admin logo após publicar."""
        resposta = self.autenticado().put(
            "/api/catalogo", {"cats": self.catalogo_atual()}, format="json"
        )
        ids = [c["id"] for c in resposta.json()["cats"]]
        self.assertIn("apn", ids)
        self.assertEqual(ids, ["elite", "treinamentos", "apn", "palestras"])

    def test_publicacao_registra_autor_e_alteracoes(self):
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "evo")["monthly"] = 31000

        self.autenticado().put("/api/catalogo", {"cats": cats}, format="json")

        publicacao = PublicacaoCatalogo.objects.order_by("-publicado_em").first()
        self.assertEqual(publicacao.autor, self.usuario)
        self.assertEqual(publicacao.autor_email, "diretoria@sejaap.com.br")
        self.assertIn("ELITE EVO", publicacao.resumo)
        self.assertIn("31000", publicacao.resumo)

    def test_atualizado_em_deixa_de_ser_nulo_depois_de_publicar(self):
        self.assertIsNone(self.client.get("/api/catalogo").json()["atualizadoEm"])
        self.autenticado().put(
            "/api/catalogo", {"cats": self.catalogo_atual()}, format="json"
        )
        self.assertIsNotNone(self.client.get("/api/catalogo").json()["atualizadoEm"])

    def test_publicar_sem_mudar_nada_nao_lista_alteracoes(self):
        resposta = self.autenticado().put(
            "/api/catalogo", {"cats": self.catalogo_atual()}, format="json"
        )
        self.assertEqual(resposta.json()["alteracoes"], [])


class ValidacaoTest(BaseEscrita):
    """As mensagens são as do validador do KV — o admin as exibe cruas."""

    def publica(self, cats):
        return self.autenticado().put("/api/catalogo", {"cats": cats}, format="json")

    def test_mensalidade_zero_e_recusada(self):
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "pro")["monthly"] = 0
        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Mensalidade inválida", resposta.json()["erro"])

    def test_mensalidade_acima_do_teto_e_recusada(self):
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "pro")["monthly"] = 10_000_001
        self.assertEqual(self.publica(cats).status_code, 400)

    def test_valor_de_avulso_zero_e_recusado(self):
        cria_avulso()
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "intensivo")["price"] = 0
        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Valor inválido", resposta.json()["erro"])

    def test_vigencia_divergente_e_recusada(self):
        """O admin não edita vigência; divergência é tela velha ou payload
        adulterado. Recalcular o total por cima seria pior."""
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "pro")["vigencia"] = 24
        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Vigência", resposta.json()["erro"])

    def test_produto_desconhecido_e_recusado_sem_criar_linha(self):
        cats = self.catalogo_atual()
        categoria = next(c for c in cats if c["id"] == "elite")
        categoria["products"].append(
            {"id": "novo", "name": "Inventado", "price": 1000}
        )
        antes = Produto.objects.count()
        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Produto.objects.count(), antes)

    def test_categoria_sem_a_lista_de_produtos_e_recusada(self):
        """Corpo malformado — tela que perdeu metade do estado."""
        cats = self.catalogo_atual()
        del next(c for c in cats if c["id"] == "elite")["products"]
        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("sem a lista de produtos", resposta.json()["erro"])

    def test_categoria_com_lista_vazia_e_aceita(self):
        """Lista vazia é legítima desde 17/08/2026.

        Treinamentos e Palestras existem travadas e SEM produto, esperando os de
        verdade. Antes disso toda categoria tinha produto e o vazio só podia ser
        erro — o guarda recusava os dois casos juntos.
        """
        cats = self.catalogo_atual()
        self.assertEqual(next(c for c in cats if c["id"] == "treinamentos")["products"], [])
        self.assertEqual(self.publica(cats).status_code, 200)

    def test_categoria_duplicada_e_recusada(self):
        cats = self.catalogo_atual()
        cats.append(next(c for c in cats if c["id"] == "elite"))
        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Categoria duplicada", resposta.json()["erro"])

    def test_corpo_vazio_e_recusado(self):
        resposta = self.autenticado().put("/api/catalogo", {}, format="json")
        self.assertEqual(resposta.status_code, 400)

    def test_so_a_apn_no_corpo_e_recusado(self):
        """Descartada a categoria de fluxo próprio, não sobra nada para publicar."""
        cats = [c for c in self.catalogo_atual() if c["id"] == "apn"]
        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)

    def test_recusa_nao_deixa_publicacao_pela_metade(self):
        """A transação é a mesma promessa do `normalizaCatalogo`: tudo ou nada.

        O produto inválido vem DEPOIS de uma alteração válida — se a escrita não
        fosse atômica, a primeira teria sido gravada. (Antes de 17/08/2026 o
        inválido ficava na última categoria; Palestras não tem mais produto, então
        o par agora é `pro` → `conselho`, o último da Elite.)
        """
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "pro")["monthly"] = 15000
        self._acha(cats, "elite", "conselho")["monthly"] = -1

        resposta = self.publica(cats)
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Produto.objects.get(slug="pro").mensalidade, Decimal("12997.00"))
        self.assertEqual(PublicacaoCatalogo.objects.count(), 0)

    def test_erro_sempre_devolve_json(self):
        """O `admin.html:174` faz `await r.json()` antes de checar `r.ok`."""
        cats = self.catalogo_atual()
        self._acha(cats, "elite", "pro")["monthly"] = -5
        resposta = self.publica(cats)
        self.assertEqual(resposta["Content-Type"].split(";")[0], "application/json")
        self.assertIn("erro", resposta.json())
