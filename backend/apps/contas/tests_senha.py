"""
As duas metades do ciclo da senha: `POST /api/senha/esqueci` pede o link e
`POST /api/senha/definir` o consome.

O serviço central é mockado: o que se verifica aqui é o que o LOBBY faz com
cada resposta dele. A tradução de erros importa tanto quanto no login, e por um
motivo simétrico: quem está nesta tela não consegue entrar em lugar nenhum, e
uma mensagem errada aqui a deixa presa do lado de fora sem saber o que tentar.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from identidade_client import (
    ErroIdentidade,
    IdentidadeIndisponivel,
    SenhaFraca,
    TokenInvalido,
)

from .throttling import DefinicaoSenhaThrottle

ALVO = "apps.contas.views.ClienteIdentidade"
TOKEN = "um-token-de-32-bytes-em-base64url"
SENHA = "uma-senha-bem-comprida"


def _com_teto(rate):
    """Troca o teto de `definir_senha` durante o bloco.

    Mexe direto no `THROTTLE_RATES` da classe, e não por `override_settings`,
    por um detalhe do DRF: `SimpleRateThrottle.THROTTLE_RATES` é atributo de
    CLASSE, resolvido uma vez no import. Sobrescrever `REST_FRAMEWORK` recarrega
    o `api_settings`, mas a classe continua apontando para o dicionário antigo —
    e o teste rodaria com o teto de produção, dando verde sem testar nada.
    """
    return patch.dict(DefinicaoSenhaThrottle.THROTTLE_RATES, {"definir_senha": rate})


@override_settings(AUTH_CENTRAL_ATIVO=True)
class DefinirSenhaTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        # O contador do throttle vive no cache do PROCESSO, e o rollback de
        # transação do TestCase não o desfaz. Mesmo motivo do tests_sessao.
        cache.clear()

    def definir(self, token=TOKEN, senha=SENHA):
        corpo = {}
        if token is not None:
            corpo["token"] = token
        if senha is not None:
            corpo["senha_nova"] = senha
        return self.client.post("/api/senha/definir", corpo, format="json")

    # ----- caminho feliz -------------------------------------------------
    def test_define_a_senha_no_conecta_id(self):
        with patch(ALVO) as Cliente:
            resposta = self.definir()

        self.assertEqual(resposta.status_code, 200, resposta.data)
        Cliente.return_value.definir_senha.assert_called_once_with(TOKEN, SENHA)

    def test_nao_exige_credencial(self):
        """É o caminho de quem não consegue entrar — exigir login seria círculo."""
        with patch(ALVO):
            resposta = APIClient().post(
                "/api/senha/definir",
                {"token": TOKEN, "senha_nova": SENHA},
                format="json",
            )
        self.assertEqual(resposta.status_code, 200)

    def test_espaco_em_volta_do_token_nao_atrapalha(self):
        """Copiar o link do WhatsApp costuma trazer espaço junto."""
        with patch(ALVO) as Cliente:
            self.definir(token=f"  {TOKEN}  ")

        Cliente.return_value.definir_senha.assert_called_once_with(TOKEN, SENHA)

    # ----- recusas --------------------------------------------------------
    def test_token_morto_nao_diz_qual_dos_tres_motivos(self):
        """Inexistente, expirado e já usado devolvem a MESMA frase.

        Diferenciar contaria a quem chuta que aquele formato de token existe.
        """
        with patch(ALVO) as Cliente:
            Cliente.return_value.definir_senha.side_effect = TokenInvalido("expirou")
            resposta = self.definir()

        self.assertEqual(resposta.status_code, 400)
        self.assertNotIn("expirou", resposta.data["erro"])
        self.assertIn("não vale mais", resposta.data["erro"])

    def test_senha_fraca_diz_o_que_corrigir(self):
        """Aqui o detalhe É útil: é a pessoa escolhendo a senha dela."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.definir_senha.side_effect = SenhaFraca(
                "Esta senha é muito curta. Ela precisa conter no mínimo 10 caracteres."
            )
            resposta = self.definir(senha="curta")

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("10 caracteres", resposta.data["erro"])

    def test_servico_fora_do_ar_vira_503_e_nunca_link_invalido(self):
        """503 e não 400: 'link inválido' faria a pessoa pedir outro à toa."""
        with patch(ALVO) as Cliente:
            Cliente.return_value.definir_senha.side_effect = IdentidadeIndisponivel()
            with self.assertLogs("apps.contas.views", level="ERROR"):
                resposta = self.definir()

        self.assertEqual(resposta.status_code, 503)
        self.assertNotIn("não vale mais", resposta.data["erro"])

    def test_campos_faltando_viram_400_sem_chamar_o_servico(self):
        with patch(ALVO) as Cliente:
            self.assertEqual(self.definir(senha=None).status_code, 400)
            self.assertEqual(self.definir(token=None).status_code, 400)
            self.assertEqual(self.definir(token="   ").status_code, 400)

        Cliente.return_value.definir_senha.assert_not_called()

    # ----- teto -----------------------------------------------------------
    def test_tem_teto_proprio(self):
        """Endpoint anônimo que grava credencial não pode ser ilimitado."""
        with _com_teto("2/hour"):
            with patch(ALVO):
                self.assertEqual(self.definir().status_code, 200)
                self.assertEqual(self.definir().status_code, 200)
                resposta = self.definir()

        self.assertEqual(resposta.status_code, 429)
        # O handler do app precisa ter traduzido o 429 do DRF para {"erro": ...}.
        self.assertIn("erro", resposta.data)


@override_settings(AUTH_CENTRAL_ATIVO=True)
class EsqueciSenhaTest(TestCase):
    """Pedir o link — a metade que faltava até 28/08/2026.

    Antes dela, o consultor que esquecia a senha dependia da diretoria gerar o
    link no kanban, um sistema em que boa parte de quem vende não entra.

    O que estes testes guardam, acima de tudo, é a UNIFORMIDADE da resposta:
    esta é a rota mais exposta do Lobby depois do login, e qualquer diferença
    entre "e-mail que existe" e "e-mail que não existe" entrega a lista de quem
    trabalha na empresa a quem tiver paciência de testar endereços.
    """

    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def pedir(self, email="gerente@sejaap.com.br"):
        corpo = {} if email is None else {"email": email}
        return self.client.post("/api/senha/esqueci", corpo, format="json")

    def test_pede_o_link_ao_conecta_id(self):
        with patch(ALVO) as Cliente:
            resposta = self.pedir()

        self.assertEqual(resposta.status_code, 200, resposta.data)
        Cliente.return_value.esqueci_senha.assert_called_once_with("gerente@sejaap.com.br")

    def test_nao_exige_credencial(self):
        """É o caminho de quem não consegue entrar — exigir login seria círculo."""
        with patch(ALVO):
            resposta = APIClient().post(
                "/api/senha/esqueci", {"email": "gerente@sejaap.com.br"}, format="json"
            )
        self.assertEqual(resposta.status_code, 200)

    def test_endereco_desconhecido_responde_exatamente_igual(self):
        with patch(ALVO):
            conhecido = self.pedir("gerente@sejaap.com.br")
            cache.clear()
            estranho = self.pedir("ninguem-desse-mundo@sejaap.com.br")

        self.assertEqual(conhecido.status_code, estranho.status_code)
        self.assertEqual(conhecido.data, estranho.data)

    def test_espaco_em_volta_do_email_nao_atrapalha(self):
        """Copiar o e-mail de outro lugar costuma trazer espaço junto."""
        with patch(ALVO) as Cliente:
            self.pedir("  gerente@sejaap.com.br  ")

        Cliente.return_value.esqueci_senha.assert_called_once_with("gerente@sejaap.com.br")

    def test_sem_email_vira_400_sem_chamar_o_servico(self):
        with patch(ALVO) as Cliente:
            resposta = self.pedir(email=None)

        self.assertEqual(resposta.status_code, 400)
        Cliente.return_value.esqueci_senha.assert_not_called()

    def test_erro_do_servico_vira_503_e_nunca_sucesso(self):
        """Dizer "enviamos" com o serviço fora faria a pessoa esperar em vão."""
        with self.assertLogs("apps.contas.views", level="ERROR"):
            with patch(ALVO) as Cliente:
                Cliente.return_value.esqueci_senha.side_effect = ErroIdentidade("chave recusada")
                resposta = self.pedir()

        self.assertEqual(resposta.status_code, 503)

    def test_tem_teto_proprio(self):
        """Sem teto, a rota vira um canhão de e-mail apontado para qualquer caixa."""
        with _com_teto("2/hour"):
            with patch(ALVO):
                self.assertEqual(self.pedir().status_code, 200)
                self.assertEqual(self.pedir().status_code, 200)
                resposta = self.pedir()

        self.assertEqual(resposta.status_code, 429)
        self.assertIn("erro", resposta.data)

    def test_o_lobby_nao_redefine_mais_senha_sem_token(self):
        """A rota que fazia isso saiu do ar no Conecta ID em 28/08/2026.

        O método sumiu do cliente junto; este teste é a rede para o dia em que
        alguém recopiar uma versão antiga do `identidade_client.py`.
        """
        from identidade_client import ClienteIdentidade

        self.assertFalse(hasattr(ClienteIdentidade, "redefinir_sem_token"))


class CentralDesligadaTest(TestCase):
    @override_settings(AUTH_CENTRAL_ATIVO=False)
    def test_esqueci_senha_responde_503(self):
        """Sem a central não há link para pedir — e falhar calado seria pior."""
        with self.assertLogs("apps.contas.views", level="ERROR"):
            resposta = APIClient().post(
                "/api/senha/esqueci", {"email": "gerente@sejaap.com.br"}, format="json"
            )
        self.assertEqual(resposta.status_code, 503)

    @override_settings(AUTH_CENTRAL_ATIVO=False)
    def test_definir_senha_responde_503(self):
        """Sem a central não há senha para definir — e falhar calado seria pior."""
        with self.assertLogs("apps.contas.views", level="ERROR"):
            resposta = APIClient().post(
                "/api/senha/definir",
                {"token": TOKEN, "senha_nova": SENHA},
                format="json",
            )
        self.assertEqual(resposta.status_code, 503)
