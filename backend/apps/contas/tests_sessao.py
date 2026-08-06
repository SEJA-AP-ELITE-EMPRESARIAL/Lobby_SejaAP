"""
Login pelo Conecta ID.

O serviço central é mockado: estes testes verificam o que o LOBBY faz com cada
resposta, não o Conecta ID em si — que tem os testes dele no repositório dele.

A tabela de tradução de erros é o coração daqui. Ela existe porque cada caso
manda o usuário para uma ação diferente, e trocar um pelo outro custa caro:
"senha incorreta" quando o serviço está fora do ar faz a empresa inteira
redefinir senha à toa.
"""
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from identidade_client import (
    BloqueadoTemporariamente,
    CredencialInvalida,
    IdentidadeIndisponivel,
    SemAcessoAoApp,
)

from .models import Papel, VinculoIdentidade

ID_FULANO = str(uuid.uuid4())
IDENTIDADE_OK = {
    "identidade_id": ID_FULANO,
    "email": "fulano@sejaap.com.br",
    "nome": "Fulano de Tal",
    "precisa_trocar_senha": False,
}

ALVO = "apps.contas.views.ClienteIdentidade"


@override_settings(AUTH_CENTRAL_ATIVO=True)
class LoginTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        # O contador do throttle vive no cache, e o LocMemCache é do PROCESSO —
        # o rollback de transação do TestCase não o desfaz. Sem limpar, os
        # logins de um teste contam para o seguinte e a suíte bate em 429.
        # É o mesmo motivo pelo qual o teto em produção é aproximado enquanto
        # não houver LOBBY_REDIS_URL.
        cache.clear()

    def entrar(self, email="fulano@sejaap.com.br", senha="segredo"):
        return self.client.post(
            "/api/sessao", {"email": email, "senha": senha}, format="json"
        )

    # ----- caminho feliz -------------------------------------------------

    def _com_papel(self, papel):
        """Primeiro login cria a conta; depois a diretoria promove."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            self.entrar()
        vinculo = VinculoIdentidade.objects.get(identidade_id=ID_FULANO)
        vinculo.papel = papel
        vinculo.save()
        return vinculo

    def test_login_valido_devolve_token_e_papel(self):
        self._com_papel(Papel.GERENTE)
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            resposta = self.entrar()

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["token"])
        self.assertEqual(corpo["email"], "fulano@sejaap.com.br")
        self.assertEqual(corpo["nome"], "Fulano de Tal")
        self.assertEqual(corpo["papel"], "gerente")
        self.assertTrue(corpo["podeAutorizarNegociacao"])
        self.assertFalse(corpo["podePublicarTabela"])

    def test_exp_vem_em_segundos(self):
        """O front faz `raw.exp * 1000 > Date.now()` (index.html:182).

        Em milissegundos, a sessão pareceria válida por 50 mil anos.
        """
        self._com_papel(Papel.GERENTE)
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            corpo = self.entrar().json()
        # ~1.7e9 em segundos hoje; em milissegundos passaria de 1e12.
        self.assertLess(corpo["exp"], 10**11)

    def test_primeiro_login_cria_conta_sem_papel_e_recusa(self):
        """Quem entra pela primeira vez não autoriza nada até ser promovido."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            resposta = self.entrar()

        self.assertEqual(resposta.status_code, 403)
        self.assertIn("não tem permissão", resposta.json()["erro"])
        # Mas a conta e o vínculo existem, para a diretoria poder promover.
        vinculo = VinculoIdentidade.objects.get(identidade_id=ID_FULANO)
        self.assertEqual(vinculo.papel, "")
        self.assertEqual(vinculo.usuario.email, "fulano@sejaap.com.br")

    def test_conta_local_nasce_sem_senha_utilizavel(self):
        """Senha local seria uma segunda porta, fora da política central."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            self.entrar()
        usuario = User.objects.get(email="fulano@sejaap.com.br")
        self.assertFalse(usuario.has_usable_password())

    def test_ip_do_usuario_final_e_repassado(self):
        """Sem isso o Conecta ID vê sempre o IP do container, e o bloqueio por
        origem vira bloqueio geral."""
        self._com_papel(Papel.GERENTE)
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            self.client.post(
                "/api/sessao",
                {"email": "fulano@sejaap.com.br", "senha": "x"},
                format="json",
                HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1",
            )
            _, kwargs = Cliente.return_value.verificar.call_args
            self.assertEqual(kwargs["ip"], "203.0.113.9")

    def test_email_e_normalizado_para_minusculas(self):
        self._com_papel(Papel.GERENTE)
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            self.entrar(email="  FULANO@SejaAP.com.BR  ")
            args, _ = Cliente.return_value.verificar.call_args
            self.assertEqual(args[0], "fulano@sejaap.com.br")

    # ----- a tabela de tradução de erros ---------------------------------

    def _erro(self, excecao):
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.side_effect = excecao
            return self.entrar()

    def test_credencial_invalida_vira_401(self):
        resposta = self._erro(CredencialInvalida("x"))
        self.assertEqual(resposta.status_code, 401)
        self.assertIn("incorretos", resposta.json()["erro"])

    def test_sem_acesso_ao_app_vira_403_com_mensagem_propria(self):
        """Não é "senha incorreta".

        Quem tem conta no Conecta ID mas não recebeu acesso ao Lobby digitaria a
        senha certa e leria "senha incorreta" — e o desfecho previsível é
        redefinir a senha, falhar de novo e abrir chamado. O Conecta ID só
        devolve este erro DEPOIS de a senha conferir, então dizê-lo em voz alta
        não entrega nada a quem não tem a credencial.
        """
        resposta = self._erro(SemAcessoAoApp("x"))
        self.assertEqual(resposta.status_code, 403)
        self.assertIn("não tem acesso ao Lobby", resposta.json()["erro"])

    def test_bloqueado_vira_429(self):
        resposta = self._erro(BloqueadoTemporariamente("x"))
        self.assertEqual(resposta.status_code, 429)
        self.assertIn("tentativas", resposta.json()["erro"])

    def test_servico_fora_do_ar_vira_503_e_NUNCA_senha_incorreta(self):
        """A regra mais importante deste arquivo.

        Se o Conecta ID cai e todo mundo lê "senha incorreta" ao mesmo tempo, a
        leitura natural é vazamento de credenciais — e vem uma enxurrada de
        trocas de senha que não resolve nada.
        """
        resposta = self._erro(IdentidadeIndisponivel("x"))
        self.assertEqual(resposta.status_code, 503)
        erro = resposta.json()["erro"].lower()
        self.assertIn("indisponível", erro)
        self.assertNotIn("senha", erro)
        self.assertNotIn("incorret", erro)

    def test_campos_faltando_viram_400(self):
        self.assertEqual(
            self.client.post("/api/sessao", {"email": "a@b.c"}, format="json").status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/sessao", {"senha": "x"}, format="json").status_code,
            400,
        )


class CentralDesligadaTest(TestCase):
    """`AUTH_CENTRAL_ATIVO=False` não é rollback — é tranca.

    Não existe senha local neste app, então desligar não devolve o login antigo:
    tira todo mundo. Falhar com 503 e log de erro é melhor que 401, que mandaria
    a diretoria caçar uma senha que não existe.
    """

    @override_settings(AUTH_CENTRAL_ATIVO=False)
    def test_login_responde_503(self):
        resposta = APIClient().post(
            "/api/sessao", {"email": "a@b.c", "senha": "x"}, format="json"
        )
        self.assertEqual(resposta.status_code, 503)

    @override_settings(AUTH_CENTRAL_ATIVO=False)
    def test_mas_o_catalogo_publico_continua_de_pe(self):
        """O consultor não pode perder a cotação porque o login caiu."""
        self.assertEqual(APIClient().get("/api/catalogo").status_code, 200)


@override_settings(AUTH_CENTRAL_ATIVO=True)
class PontaAPontaTest(TestCase):
    """Login de verdade → token de verdade → publicação de verdade.

    Os outros testes de escrita usam `force_authenticate`, que pula a camada de
    JWT. Este não pula nada: se o token emitido pelo /api/sessao não for aceito
    pelo /api/catalogo, é aqui que aparece.
    """

    def _logar(self, papel):
        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            self.client.post(
                "/api/sessao",
                {"email": IDENTIDADE_OK["email"], "senha": "x"},
                format="json",
            )
        vinculo = VinculoIdentidade.objects.get(identidade_id=ID_FULANO)
        vinculo.papel = papel
        vinculo.save()

        with patch(ALVO) as Cliente:
            Cliente.return_value.verificar.return_value = IDENTIDADE_OK
            resposta = self.client.post(
                "/api/sessao",
                {"email": IDENTIDADE_OK["email"], "senha": "x"},
                format="json",
            )
        return resposta.json()["token"]

    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def test_diretoria_loga_e_publica_com_o_token_emitido(self):
        token = self._logar(Papel.DIRETORIA)

        cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
        cats = self.client.get("/api/catalogo").json()["cats"]
        pro = next(p for p in cats[0]["products"] if p["id"] == "pro")
        pro["monthly"] = 14000

        resposta = cliente.put("/api/catalogo", {"cats": cats}, format="json")
        self.assertEqual(resposta.status_code, 200)

        publicado = next(
            p for p in resposta.json()["cats"][0]["products"] if p["id"] == "pro"
        )
        self.assertEqual(publicado["monthly"], 14000)
        self.assertEqual(publicado["price"], 14000 * 12)

    def test_gerente_loga_mas_o_token_dele_nao_publica(self):
        token = self._logar(Papel.GERENTE)

        cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
        resposta = cliente.put(
            "/api/catalogo",
            {"cats": self.client.get("/api/catalogo").json()["cats"]},
            format="json",
        )
        self.assertEqual(resposta.status_code, 403)

    def test_o_historico_registra_quem_publicou(self):
        """O que o KV nunca teve: autor. Era só `atualizadoEm`, sem nome."""
        from apps.catalogo.models import PublicacaoCatalogo

        token = self._logar(Papel.DIRETORIA)
        cliente = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
        cats = self.client.get("/api/catalogo").json()["cats"]
        next(p for p in cats[0]["products"] if p["id"] == "evo")["monthly"] = 31000
        cliente.put("/api/catalogo", {"cats": cats}, format="json")

        publicacao = PublicacaoCatalogo.objects.latest("publicado_em")
        self.assertEqual(publicacao.autor_email, "fulano@sejaap.com.br")
        self.assertIn("ELITE EVO", publicacao.resumo)


@override_settings(AUTH_CENTRAL_ATIVO=True)
class SessaoAtualTest(TestCase):
    def test_anonimo_nao_ve_sessao(self):
        self.assertEqual(APIClient().get("/api/sessao/atual").status_code, 401)

    def test_logado_ve_nome_e_papel(self):
        usuario = User.objects.create_user(
            username="d@sejaap.com.br", email="d@sejaap.com.br",
            first_name="Ciclana", last_name="Silva",
        )
        VinculoIdentidade.objects.create(
            usuario=usuario, identidade_id=uuid.uuid4(), papel=Papel.DIRETORIA
        )
        cliente = APIClient()
        cliente.force_authenticate(user=usuario)

        corpo = cliente.get("/api/sessao/atual").json()
        self.assertEqual(corpo["nome"], "Ciclana Silva")
        self.assertEqual(corpo["papel"], "diretoria")
        self.assertTrue(corpo["podePublicarTabela"])
