"""
A lista fechada de rotas do Lobby para a asserção (ADR-002 §13.4).

1. Cada rota da lista atende a diretoria pela asserção, e o autor do que se grava
   é a pessoa do `sub`.
2. Fora da lista, 403 `ROTA_NAO_LIBERADA` — mesmo com asserção perfeita e mesmo
   que a pessoa, logada no /admin, pudesse usar a rota.
3. Quem não é diretoria aqui recebe 403 `SEM_PERMISSAO`: o papel é do Lobby.
4. Integração desligada não muda nada para quem não manda o cabeçalho: o
   consultor anônimo e o /admin de hoje seguem iguais.
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.catalogo.models import PoliticaCobranca, Vigencia
from apps.contas.tests_assercao import BaseAssercao, ParDeTeste, conjunto

COBRANCA_GERAL = {"dia_vencimento": 10, "primeiro_vencimento": "mes_seguinte", "entrada_prazo_dias": 1}


class ListaTest(BaseAssercao):
    def test_cada_rota_da_lista_atende_a_diretoria(self):
        rotas = [
            ("get", "/api/catalogo", None),
            ("get", "/api/cobranca", None),
            ("get", "/api/departamentos/configuracao", None),
            ("get", "/api/categorias-financeiras/configuracao", None),
            ("get", "/api/dh-avaliacao/valores/configuracao", None),
            ("get", "/api/publicacoes", None),
            ("get", "/api/integracao/quem-sou", None),
            ("get", "/api/integracao/saude", None),
        ]
        for metodo, caminho, corpo in rotas:
            with self.subTest(caminho=caminho):
                resposta = self.chama(metodo, caminho, self.par.assina(), corpo)
                self.assertEqual(resposta.status_code, 200, resposta.content)

    def test_escrita_pela_assercao_grava_a_pessoa_do_sub(self):
        resposta = self.chama("put", "/api/cobranca", self.par.assina(), {"geral": COBRANCA_GERAL, "excecoes": []})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        vigencia = Vigencia.objects.get(chave="geral", campo="dia_vencimento", vigente_ate__isnull=True)
        self.assertEqual(vigencia.autor, self.diretoria)
        self.assertEqual(vigencia.autor_email, "diretoria@sejaap.com.br")

    def test_quem_nao_e_diretoria_recebe_403_sem_permissao(self):
        token = self.par.assina("gerente@sejaap.com.br")
        resposta = self.chama("put", "/api/cobranca", token, {"geral": COBRANCA_GERAL, "excecoes": []})
        self.assertRecusa(resposta, 403, "SEM_PERMISSAO")
        self.assertEqual(PoliticaCobranca.geral_atual().dia_vencimento, 15)

    def test_leitura_restrita_tambem_e_do_papel(self):
        resposta = self.chama("get", "/api/publicacoes", self.par.assina("gerente@sejaap.com.br"))
        self.assertRecusa(resposta, 403, "SEM_PERMISSAO")

    def test_400_da_rota_da_lista_traz_codigo(self):
        resposta = self.chama("put", "/api/cobranca", self.par.assina(), {"geral": {"dia_vencimento": 31}})
        self.assertRecusa(resposta, 400, "DADOS_INVALIDOS")


class ForaDaListaTest(BaseAssercao):
    def test_put_do_catalogo_fica_fora_mesmo_para_a_diretoria(self):
        """Substituído por /api/integracao/catalogo/publicar (§13.4)."""
        resposta = self.chama("put", "/api/catalogo", self.par.assina(), {"cats": []})
        self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")

    def test_rotas_fora_da_lista(self):
        rotas = [
            ("patch", "/api/produtos/elite/pro", {"nome": "X"}),
            ("post", "/api/sessao", {"email": "a@b.c", "senha": "x"}),
            ("get", "/api/sessao/atual", None),
            ("post", "/api/senha/esqueci", {"email": "a@b.c"}),
            ("post", "/api/venda/comprovante", {}),
            ("post", "/api/venda/validar", {}),
            ("get", "/api/historico", None),
            ("get", "/api/departamentos", None),
            ("get", "/django-admin/", None),
            ("get", "/metrics", None),
        ]
        for metodo, caminho, corpo in rotas:
            with self.subTest(caminho=caminho):
                resposta = self.chama(metodo, caminho, self.par.assina(), corpo)
                self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")

    def test_delete_em_rota_da_lista(self):
        resposta = self.chama("delete", "/api/cobranca", self.par.assina())
        self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")

    def test_metodo_fora_da_lista_em_rota_nova(self):
        resposta = self.chama("post", "/api/integracao/quem-sou", self.par.assina(), {})
        self.assertRecusa(resposta, 403, "ROTA_NAO_LIBERADA")


class ImpersonacaoNaListaTest(BaseAssercao):
    def test_escrita_em_impersonacao_e_403_e_nao_grava(self):
        token = self.par.assina(imp=True, act=3)
        resposta = self.chama("put", "/api/cobranca", token, {"geral": COBRANCA_GERAL, "excecoes": []})
        self.assertRecusa(resposta, 403, "IMPERSONACAO_RECUSADA")
        self.assertEqual(PoliticaCobranca.geral_atual().dia_vencimento, 15)

    def test_leitura_em_impersonacao_passa(self):
        resposta = self.chama("get", "/api/departamentos/configuracao", self.par.assina(imp=True, act=3))
        self.assertEqual(resposta.status_code, 200)


@override_settings(DEBUG=True, CONECTA_ASSERCAO_CHAVES="")
class DesligadaNaoAtrapalhaTest(TestCase):
    """Sem a chave pública: integração desligada; o /admin atual segue igual."""

    def setUp(self):
        from apps.catalogo.tests_escrita import cria_pessoa
        from apps.contas.models import Papel

        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)

    def test_consultor_anonimo_le_o_catalogo(self):
        self.assertEqual(APIClient().get("/api/catalogo").status_code, 200)
        self.assertEqual(APIClient().get("/api/departamentos").status_code, 200)

    def test_admin_de_hoje_publica_com_o_jwt_de_sessao(self):
        token = RefreshToken.for_user(self.diretoria).access_token
        cliente = APIClient()
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resposta = cliente.put("/api/cobranca", {"geral": COBRANCA_GERAL, "excecoes": []}, format="json")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.json().get("codigo"), None)

    def test_admin_sem_sessao_continua_recebendo_401_sem_codigo(self):
        """O 401 derruba a sessão do `admin.html`; o corpo dele não muda."""
        resposta = APIClient().put("/api/cobranca", {}, format="json")
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(set(resposta.json()), {"erro"})

    def test_quem_manda_o_cabecalho_recebe_503(self):
        token = ParDeTeste().assina()
        resposta = APIClient().get("/api/catalogo", HTTP_X_CONECTA_ASSERCAO=token)
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json()["codigo"], "INTEGRACAO_DESLIGADA")

    def test_ligar_depois_nao_exige_reinicio_nos_testes(self):
        par = ParDeTeste()
        with override_settings(CONECTA_ASSERCAO_CHAVES=conjunto(par)):
            resposta = APIClient().get("/api/catalogo", HTTP_X_CONECTA_ASSERCAO=par.assina())
        self.assertEqual(resposta.status_code, 200)
