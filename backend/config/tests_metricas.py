"""
O /metrics, testado pelos dois lados: o que ele não pode entregar a quem não
deve, e o que ele precisa dizer a quem coleta.

O segundo importa mais do que parece. Um exporter que responde 200 com números
errados é pior do que exporter nenhum: o alerta fica calado e todo mundo acha
que está monitorado.
"""
import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalogo.models import PublicacaoCatalogo
from apps.contas.models import Papel
from apps.catalogo.tests_escrita import cria_pessoa
from apps.vendas.models import ComprovanteVenda

TOKEN = "token-de-coleta-para-teste"


def cria_comprovante(*, consumido=False, expirado=False, negociado=False):
    agora = timezone.now()
    comprovante = ComprovanteVenda.objects.create(
        protocolo="PRO-260807000001",
        fluxo=ComprovanteVenda.Fluxo.ELITE,
        negociado=negociado,
        valores={"valor_total": 155964},
        hash_valores="0" * 64,
        expira_em=agora - timedelta(minutes=1) if expirado else agora + timedelta(minutes=15),
    )
    if consumido:
        comprovante.consumido_em = agora
        comprovante.save(update_fields=["consumido_em"])
    return comprovante


@override_settings(LOBBY_METRICS_TOKEN=TOKEN)
class ColetaTest(TestCase):
    def coletar(self, auth=f"Bearer {TOKEN}"):
        return self.client.get("/metrics", HTTP_AUTHORIZATION=auth)

    def linhas(self):
        return self.coletar().content.decode().splitlines()

    def valor_de(self, prefixo):
        """O número da primeira linha que começa com `prefixo`."""
        for linha in self.linhas():
            if linha.startswith(prefixo) and not linha.startswith("#"):
                return float(linha.rsplit(" ", 1)[1])
        raise AssertionError(f"série ausente: {prefixo}")

    def test_coleta_autorizada_responde_no_formato_do_prometheus(self):
        resposta = self.coletar()
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("text/plain", resposta["Content-Type"])
        self.assertIn("lobby_up 1", resposta.content.decode())

    def test_comprovantes_sao_contados_por_estado(self):
        cria_comprovante()
        cria_comprovante(consumido=True)
        cria_comprovante(negociado=True)
        cria_comprovante(expirado=True)

        self.assertEqual(self.valor_de('lobby_comprovantes_total{estado="emitido"}'), 4)
        self.assertEqual(self.valor_de('lobby_comprovantes_total{estado="consumido"}'), 1)
        self.assertEqual(self.valor_de('lobby_comprovantes_total{estado="negociado"}'), 1)
        self.assertEqual(
            self.valor_de('lobby_comprovantes_total{estado="expirado_sem_uso"}'), 1
        )

    def test_comprovante_consumido_nao_conta_como_expirado_sem_uso(self):
        """O sinal de 'o n8n parou de validar' é justamente este par.

        Se um comprovante consumido também contasse como expirado sem uso, o
        alerta acusaria validação parada em operação normal — e seria desligado
        por barulho na primeira semana.
        """
        comprovante = cria_comprovante(consumido=True, expirado=True)
        self.assertIsNotNone(comprovante.consumido_em)
        self.assertEqual(
            self.valor_de('lobby_comprovantes_total{estado="expirado_sem_uso"}'), 0
        )

    def test_sem_venda_nenhuma_as_series_de_frescor_nao_saem(self):
        """Melhor série ausente do que zero mentindo que acabou de acontecer."""
        corpo = self.coletar().content.decode()
        self.assertNotIn("lobby_segundos_desde_ultimo_comprovante", corpo)
        self.assertNotIn("lobby_segundos_desde_ultima_validacao", corpo)

    def test_frescor_aparece_depois_da_primeira_venda(self):
        cria_comprovante(consumido=True)
        self.assertLess(self.valor_de("lobby_segundos_desde_ultimo_comprovante"), 60)
        self.assertLess(self.valor_de("lobby_segundos_desde_ultima_validacao"), 60)

    def test_ninguem_promovido_aparece_como_zero_na_diretoria(self):
        """O estado do corte para a .164: app no ar, ninguém consegue publicar."""
        self.assertEqual(self.valor_de('lobby_pessoas{papel="diretoria"}'), 0)

        cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)
        cria_pessoa("novato@sejaap.com.br", "")

        self.assertEqual(self.valor_de('lobby_pessoas{papel="diretoria"}'), 1)
        self.assertEqual(self.valor_de('lobby_pessoas{papel="gerente"}'), 1)
        self.assertEqual(self.valor_de('lobby_pessoas{papel="sem_papel"}'), 1)

    def test_catalogo_semeado_aparece_com_produto(self):
        """Zero produto é o estado que o front esconde caindo no fallback."""
        self.assertGreaterEqual(self.valor_de('lobby_catalogo{item="produtos"}'), 1)
        self.assertGreaterEqual(self.valor_de('lobby_catalogo{item="categorias"}'), 1)

    def test_publicacoes_sao_contadas(self):
        self.assertEqual(self.valor_de("lobby_publicacoes_total"), 0)
        PublicacaoCatalogo.objects.create(catalogo={"cats": []}, autor_email="x@y.z")
        self.assertEqual(self.valor_de("lobby_publicacoes_total"), 1)

    @override_settings(LOBBY_N8N_TOKEN="")
    def test_n8n_sem_segredo_aparece_como_desconfigurado(self):
        """Um deploy que perca a variável desliga a validação em silêncio."""
        self.assertEqual(self.valor_de("lobby_n8n_configurado"), 0)

    @override_settings(LOBBY_N8N_TOKEN="tem-segredo")
    def test_n8n_com_segredo_aparece_como_configurado(self):
        self.assertEqual(self.valor_de("lobby_n8n_configurado"), 1)


@override_settings(LOBBY_METRICS_TOKEN=TOKEN)
class AcessoTest(TestCase):
    """O /metrics conta o volume de vendas e quem pode dar desconto."""

    def test_sem_authorization_e_recusado(self):
        self.assertEqual(self.client.get("/metrics").status_code, 401)

    def test_token_errado_e_recusado(self):
        resposta = self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer outro")
        self.assertEqual(resposta.status_code, 401)

    def test_token_sem_o_prefixo_bearer_e_recusado(self):
        resposta = self.client.get("/metrics", HTTP_AUTHORIZATION=TOKEN)
        self.assertEqual(resposta.status_code, 401)

    def test_post_nao_e_aceito(self):
        resposta = self.client.post("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertEqual(resposta.status_code, 405)

    def test_coleta_nao_exige_login(self):
        """Quem coleta é o Prometheus, que não tem conta no Conecta ID."""
        resposta = self.client.get("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertEqual(resposta.status_code, 200)


class RedirecionamentoTest(TestCase):
    """O /metrics não pode ser redirecionado para https.

    Esta é a falha que só aparecia em produção: com DEBUG=True o
    SECURE_SSL_REDIRECT fica desligado, e a suíte passava inteira enquanto a
    coleta real levava 301 na VPS — alvo DOWN para sempre e o alerta de "fora
    do ar" tocando com o Lobby de pé.

    O middleware é instanciado DENTRO do override porque ele lê o
    SECURE_SSL_REDIRECT no __init__: com o handler já montado, o override não
    chegaria nele e o teste passaria sem testar nada.
    """

    def resposta_do_middleware(self, caminho):
        from django.http import HttpResponse
        from django.middleware.security import SecurityMiddleware
        from django.test import RequestFactory

        middleware = SecurityMiddleware(lambda pedido: HttpResponse())
        return middleware(RequestFactory().get(caminho))

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^metrics$"])
    def test_metrics_nao_e_redirecionado_com_o_redirect_ligado(self):
        self.assertNotEqual(self.resposta_do_middleware("/metrics").status_code, 301)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^metrics$"])
    def test_o_resto_do_site_continua_sendo_redirecionado(self):
        """Contraprova: sem isto, o teste acima passaria com o redirect quebrado."""
        self.assertEqual(self.resposta_do_middleware("/api/catalogo").status_code, 301)

    def test_a_isencao_esta_configurada_no_settings(self):
        """Prende o valor real, não só o comportamento sob override."""
        from django.conf import settings

        self.assertIn(r"^metrics$", settings.SECURE_REDIRECT_EXEMPT)


class DesabilitadoTest(TestCase):
    """Sem token configurado o endpoint não existe — nem para dizer que existe."""

    @override_settings(LOBBY_METRICS_TOKEN="")
    def test_sem_token_configurado_responde_404(self):
        self.assertEqual(self.client.get("/metrics").status_code, 404)

    @override_settings(LOBBY_METRICS_TOKEN="")
    def test_sem_token_configurado_nem_com_credencial_entrega(self):
        resposta = self.client.get("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertEqual(resposta.status_code, 404)
        self.assertNotIn("lobby_up", resposta.content.decode())
