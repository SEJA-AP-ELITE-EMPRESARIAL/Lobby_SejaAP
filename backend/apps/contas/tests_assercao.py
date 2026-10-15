"""
A asserção do Conecta (TSK-1059, ADR-002 §§4, 8, 10 e 13).

O que estes testes guardam, em ordem de dano:

1. Asserção ruim nunca entra, e nunca vira 500: vencida, futura, longa demais,
   assinada por outra chave, para outro app, repetida, sem `imp` — cada uma com o
   seu `codigo`, que é o que o Conecta lê.
2. A asserção não cria conta e não dá papel. Sem conta no Lobby, 401; quem
   decide se publica é o `VinculoIdentidade.papel` daqui.
3. Sem chave pública, ou sem cache compartilhado em produção, a integração se
   declara desligada (503) — e só para quem manda o cabeçalho.
4. A asserção nunca aparece no log.

Os pares Ed25519 nascem aqui, um por teste. Nenhuma chave mora no repositório
(ADR-002 §10.1): par fixo em repositório é o caminho por onde ele acaba em
produção.
"""
import base64
import json
import logging
import os
import subprocess
import sys
import textwrap
import time
import uuid
import warnings
from unittest import mock, skipUnless

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalogo.tests_escrita import cria_pessoa

from . import autenticacao_conecta
from .autenticacao_conecta import FalhasPorIp, IntegracaoLeituraThrottle
from .models import Papel

QUEM_SOU = "/api/integracao/quem-sou"
SAUDE = "/api/integracao/saude"


def _b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


class ParDeTeste:
    """Um par Ed25519 gerado na hora, no papel do assinador do Conecta."""

    def __init__(self, kid="conecta-202610-teste"):
        self.kid = kid
        self.privada = Ed25519PrivateKey.generate()

    def jwk(self) -> dict:
        crua = self.privada.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return {"kty": "OKP", "crv": "Ed25519", "use": "sig", "alg": "EdDSA", "kid": self.kid, "x": _b64url(crua)}

    def assina(self, sub="diretoria@sejaap.com.br", *, cabecalho=None, sem=(), **claims) -> str:
        agora = int(time.time())
        corpo = {
            "iss": "conecta",
            "aud": "lobby",
            "sub": sub,
            "iat": agora,
            "nbf": agora,
            "exp": agora + 60,
            "jti": str(uuid.uuid4()),
            "imp": False,
            "act": None,
        }
        corpo.update(claims)
        for nome in sem:
            corpo.pop(nome, None)
        cab = {"kid": self.kid, "typ": "JWT"}
        cab.update(cabecalho or {})
        return jwt.encode(corpo, self.privada, algorithm="EdDSA", headers=cab)


def cache_contando_ha_tempo():
    """O cache do `jti` como o de um Redis no ar há tempo — e não o de um
    recém-reiniciado, que fecha a integração por uns minutos (`MARCO_DO_CACHE`)."""
    cache_contando_ha_tempo_em(autenticacao_conecta._cache_do_jti(), int(time.time()))


def cache_contando_ha_tempo_em(cache_do_jti, agora):
    cache_do_jti.set(
        autenticacao_conecta.MARCO_DO_CACHE, agora - autenticacao_conecta.janela_depois_de_cache_vazio() - 1, None
    )


def conjunto(*pares) -> str:
    return json.dumps({"keys": [p.jwk() for p in pares]})


@override_settings(DEBUG=True)
class BaseAssercao(TestCase):
    """Integração ligada com uma chave de teste e o cache local.

    `DEBUG=True` porque o test runner do Django força `DEBUG=False`, e com ele o
    `LocMemCache` (por processo) desliga a integração — que é exatamente a regra
    de produção, testada à parte em `DesligadaTest`.
    """

    def setUp(self):
        # O cache guarda `jti`, o contador de recusas por IP e os tetos por
        # pessoa: sem limpar, um teste herdaria o balde do anterior.
        cache.clear()
        # O cache de cada teste nasce vazio, como um Redis recém-reiniciado — e
        # cache vazio fecha a integração por uns minutos (`MARCO_DO_CACHE`). Aqui
        # ele começa como um cache que já conta há tempo; `CacheVazioTest` cobre
        # o recém-esvaziado.
        cache_contando_ha_tempo()
        self.par = ParDeTeste()
        self.liga(self.par)
        self.diretoria = cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)
        self.gerente = cria_pessoa("gerente@sejaap.com.br", Papel.GERENTE)

    def liga(self, *pares):
        configuracao = override_settings(CONECTA_ASSERCAO_CHAVES=conjunto(*pares))
        configuracao.enable()
        self.addCleanup(configuracao.disable)

    def chama(self, metodo, caminho, token, corpo=None):
        cliente = APIClient()
        kwargs = {"HTTP_X_CONECTA_ASSERCAO": token}
        if corpo is not None:
            return getattr(cliente, metodo)(caminho, corpo, format="json", **kwargs)
        return getattr(cliente, metodo)(caminho, **kwargs)

    def get(self, caminho=QUEM_SOU, sub="diretoria@sejaap.com.br", **claims):
        return self.chama("get", caminho, self.par.assina(sub, **claims))

    def assertRecusa(self, resposta, status, codigo):
        self.assertEqual(resposta.status_code, status, resposta.content)
        corpo = resposta.json()
        self.assertEqual(corpo["codigo"], codigo)
        # O corpo do ADR e o da casa, juntos: o Conecta lê `codigo`/`detail`, as
        # telas do Lobby leem `erro`.
        self.assertTrue(corpo["detail"])
        self.assertEqual(corpo["erro"], corpo["detail"])


class AceitaTest(BaseAssercao):
    def test_diretoria_e_reconhecida_e_publica(self):
        resposta = self.get()
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(
            resposta.json(),
            {
                "nome": "diretoria@sejaap.com.br",
                "email": "diretoria@sejaap.com.br",
                "papel": "diretoria",
                "podePublicarTabela": True,
                "impersonacao": False,
            },
        )

    def test_gerente_entra_mas_nao_publica(self):
        """O papel é do Lobby: a asserção não carrega permissão nenhuma."""
        corpo = self.get(sub="gerente@sejaap.com.br").json()
        self.assertEqual(corpo["papel"], "gerente")
        self.assertFalse(corpo["podePublicarTabela"])

    def test_conta_sem_vinculo_entra_em_so_leitura(self):
        User.objects.create_user(username="sem@sejaap.com.br", email="sem@sejaap.com.br")
        corpo = self.get(sub="sem@sejaap.com.br").json()
        self.assertEqual(corpo["papel"], "")
        self.assertFalse(corpo["podePublicarTabela"])

    def test_sub_casa_sem_diferenca_de_maiusculas(self):
        resposta = self.get(sub="Diretoria@SejaAP.com.br")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["email"], "diretoria@sejaap.com.br")

    def test_tolerancia_de_relogio_de_10_segundos(self):
        agora = int(time.time())
        # Venceu há 8 s: dentro da tolerância.
        resposta = self.get(iat=agora - 68, nbf=agora - 68, exp=agora - 8)
        self.assertEqual(resposta.status_code, 200)
        # 8 s no futuro: idem.
        resposta = self.get(iat=agora + 8, nbf=agora + 8, exp=agora + 68)
        self.assertEqual(resposta.status_code, 200)

    def test_resposta_nao_e_cacheavel(self):
        self.assertEqual(self.get()["Cache-Control"], "no-store")

    def test_saude_lista_os_kids_aceitos(self):
        segunda = ParDeTeste("conecta-202611-a")
        self.liga(self.par, segunda)
        corpo = self.get(SAUDE).json()
        self.assertEqual(corpo, {"kids": ["conecta-202610-teste", "conecta-202611-a"], "emissor": "conecta", "audiencia": "lobby"})

    def test_troca_de_chave_aceita_as_duas_enquanto_as_duas_estao_configuradas(self):
        segunda = ParDeTeste("conecta-202611-a")
        self.liga(self.par, segunda)
        self.assertEqual(self.chama("get", QUEM_SOU, self.par.assina()).status_code, 200)
        self.assertEqual(self.chama("get", QUEM_SOU, segunda.assina()).status_code, 200)


class NaoCriaContaTest(BaseAssercao):
    def test_sem_conta_e_401_e_nao_cria_usuario(self):
        antes = User.objects.count()
        resposta = self.get(sub="novo@sejaap.com.br")
        self.assertRecusa(resposta, 401, "SEM_CONTA")
        self.assertEqual(User.objects.count(), antes)
        self.assertFalse(User.objects.filter(email="novo@sejaap.com.br").exists())

    def test_conta_inativa_e_401(self):
        self.gerente.is_active = False
        self.gerente.save()
        self.assertRecusa(self.get(sub="gerente@sejaap.com.br"), 401, "CONTA_INATIVA")

    def test_email_repetido_em_duas_contas_e_recusado(self):
        """Escolher uma das duas seria dar a uma pessoa o papel da outra."""
        User.objects.create_user(username="outra", email="DIRETORIA@sejaap.com.br")
        self.assertRecusa(self.get(), 401, "SEM_CONTA")


class RecusaTest(BaseAssercao):
    def test_sem_cabecalho_na_rota_nova_e_401(self):
        resposta = APIClient().get(QUEM_SOU)
        self.assertRecusa(resposta, 401, "ASSERCAO_AUSENTE")
        self.assertEqual(resposta["WWW-Authenticate"], "Conecta-Assercao")

    def test_jwt_de_sessao_nao_entra_na_rota_nova(self):
        """Rota de integração só aceita a asserção (ADR-002 §4.1)."""
        from rest_framework_simplejwt.tokens import RefreshToken

        token = RefreshToken.for_user(self.diretoria).access_token
        resposta = APIClient().get(QUEM_SOU, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertRecusa(resposta, 401, "ASSERCAO_AUSENTE")

    def test_vencida(self):
        agora = int(time.time())
        self.assertRecusa(self.get(iat=agora - 120, nbf=agora - 120, exp=agora - 60), 401, "ASSERCAO_EXPIRADA")

    def test_futura(self):
        agora = int(time.time())
        self.assertRecusa(self.get(iat=agora + 60, nbf=agora + 60, exp=agora + 120), 401, "ASSERCAO_FUTURA")

    def test_nbf_no_futuro_mesmo_com_iat_no_presente(self):
        agora = int(time.time())
        self.assertRecusa(self.get(nbf=agora + 40, exp=agora + 60), 401, "ASSERCAO_FUTURA")

    def test_validade_acima_de_70_segundos(self):
        """Bug no assinador que emitisse uma asserção de um dia não passa."""
        agora = int(time.time())
        self.assertRecusa(self.get(exp=agora + 86400), 401, "ASSERCAO_LONGA")

    def test_assinatura_de_outra_chave_com_o_mesmo_kid(self):
        impostora = ParDeTeste(self.par.kid)
        self.assertRecusa(self.chama("get", QUEM_SOU, impostora.assina()), 401, "ASSINATURA_INVALIDA")

    def test_corpo_adulterado_depois_de_assinado(self):
        cabecalho, corpo, assinatura = self.par.assina().split(".")
        claims = json.loads(base64.urlsafe_b64decode(corpo + "=="))
        claims["sub"] = "gerente@sejaap.com.br"
        adulterado = _b64url(json.dumps(claims).encode())
        self.assertRecusa(self.chama("get", QUEM_SOU, f"{cabecalho}.{adulterado}.{assinatura}"), 401, "ASSINATURA_INVALIDA")

    def test_kid_desconhecido(self):
        outra = ParDeTeste("conecta-209912-z")
        self.assertRecusa(self.chama("get", QUEM_SOU, outra.assina()), 401, "KID_DESCONHECIDO")

    def test_kid_ausente(self):
        token = jwt.encode(
            {"iss": "conecta", "aud": "lobby", "sub": "diretoria@sejaap.com.br"},
            self.par.privada,
            algorithm="EdDSA",
        )
        self.assertRecusa(self.chama("get", QUEM_SOU, token), 401, "KID_DESCONHECIDO")

    def test_hs256_com_a_chave_publica_como_segredo(self):
        """A confusão de algoritmo clássica: `algorithms` escrito à mão a barra."""
        agora = int(time.time())
        segredo = self.par.jwk()["x"]
        with warnings.catch_warnings():
            # O PyJWT avisa que o segredo é curto; aqui ele é o ataque, não um erro.
            warnings.simplefilter("ignore", jwt.warnings.InsecureKeyLengthWarning)
            token = jwt.encode(
                {"iss": "conecta", "aud": "lobby", "sub": "diretoria@sejaap.com.br", "iat": agora, "nbf": agora,
                 "exp": agora + 60, "jti": str(uuid.uuid4()), "imp": False},
                segredo,
                algorithm="HS256",
                headers={"kid": self.par.kid},
            )
            resposta = self.chama("get", QUEM_SOU, token)
        self.assertRecusa(resposta, 401, "CABECALHO_INVALIDO")

    def test_alg_none(self):
        cab = _b64url(json.dumps({"alg": "none", "typ": "JWT", "kid": self.par.kid}).encode())
        corpo = _b64url(json.dumps({"iss": "conecta", "aud": "lobby", "sub": "diretoria@sejaap.com.br"}).encode())
        self.assertRecusa(self.chama("get", QUEM_SOU, f"{cab}.{corpo}."), 401, "CABECALHO_INVALIDO")

    def test_typ_diferente_de_jwt(self):
        token = self.par.assina(cabecalho={"typ": "at+jwt"})
        self.assertRecusa(self.chama("get", QUEM_SOU, token), 401, "CABECALHO_INVALIDO")

    def test_audiencia_de_outro_app(self):
        self.assertRecusa(self.get(aud="kanban"), 401, "AUDIENCIA_ERRADA")
        self.assertRecusa(self.get(aud="formulario"), 401, "AUDIENCIA_ERRADA")

    def test_audiencia_em_lista_e_recusada_mesmo_com_lobby_dentro(self):
        self.assertRecusa(self.get(aud=["lobby"]), 401, "AUDIENCIA_ERRADA")
        self.assertRecusa(self.get(aud=["lobby", "kanban"]), 401, "AUDIENCIA_ERRADA")

    def test_audiencia_ausente(self):
        self.assertRecusa(self.get(sem=("aud",)), 401, "AUDIENCIA_ERRADA")

    def test_emissor_errado(self):
        self.assertRecusa(self.get(iss="kanban"), 401, "EMISSOR_ERRADO")

    def test_jti_repetido(self):
        token = self.par.assina()
        self.assertEqual(self.chama("get", QUEM_SOU, token).status_code, 200)
        self.assertRecusa(self.chama("get", QUEM_SOU, token), 401, "JTI_REPETIDO")

    def test_imp_ausente_e_recusado_e_nao_tratado_como_falso(self):
        self.assertRecusa(self.get(sem=("imp",)), 401, "CLAIM_FALTANDO")

    def test_imp_que_nao_e_booleano(self):
        self.assertRecusa(self.get(imp="false"), 401, "CLAIM_FALTANDO")
        self.assertRecusa(self.get(imp=0), 401, "CLAIM_FALTANDO")

    def test_cada_claim_obrigatoria(self):
        for nome in ("iss", "sub", "iat", "nbf", "exp", "jti"):
            with self.subTest(nome=nome):
                self.assertRecusa(self.get(sem=(nome,)), 401, "CLAIM_FALTANDO")

    def test_data_que_nao_e_inteiro(self):
        self.assertRecusa(self.get(exp="amanhã"), 401, "CLAIM_FALTANDO")
        self.assertRecusa(self.get(iat=True), 401, "CLAIM_FALTANDO")

    def test_sub_que_nao_e_email(self):
        self.assertRecusa(self.get(sub="diretoria"), 401, "CLAIM_FALTANDO")

    def test_lixo_no_cabecalho_nunca_vira_500(self):
        for lixo in ("", "abc", "a.b.c", "a.b", "..", "eyJ.eyJ.eyJ", "x" * 5000, "Bearer " + self.par.assina()):
            with self.subTest(lixo=lixo[:20]):
                self.assertRecusa(self.chama("get", QUEM_SOU, lixo), 401, "ASSERCAO_MAL_FORMADA")


class ImpersonacaoTest(BaseAssercao):
    def test_leitura_em_impersonacao_passa_e_avisa(self):
        resposta = self.get(imp=True, act=42)
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["impersonacao"])
        # Diretoria, mas nesta sessão as escritas seriam recusadas.
        self.assertFalse(corpo["podePublicarTabela"])

    def test_act_preenchido_conta_como_impersonacao(self):
        self.assertTrue(self.get(imp=False, act=7).json()["impersonacao"])


@override_settings(DEBUG=True)
class DesligadaTest(TestCase):
    def setUp(self):
        cache.clear()
        self.par = ParDeTeste()
        cria_pessoa("diretoria@sejaap.com.br", Papel.DIRETORIA)

    def test_sem_chave_a_rota_nova_responde_503_com_e_sem_cabecalho(self):
        with override_settings(CONECTA_ASSERCAO_CHAVES=""):
            com = APIClient().get(QUEM_SOU, HTTP_X_CONECTA_ASSERCAO=self.par.assina())
            sem = APIClient().get(QUEM_SOU)
        for resposta in (com, sem):
            self.assertEqual(resposta.status_code, 503)
            self.assertEqual(resposta.json()["codigo"], "INTEGRACAO_DESLIGADA")

    def test_chave_mal_configurada_desliga_inteira(self):
        casos = {
            "não é json": "{keys:",
            "sem keys": json.dumps({"chaves": []}),
            "lista vazia": json.dumps({"keys": []}),
            "sem kid": json.dumps({"keys": [{**self.par.jwk(), "kid": ""}]}),
            "kid repetido": json.dumps({"keys": [self.par.jwk(), self.par.jwk()]}),
            "outra curva": json.dumps({"keys": [{**self.par.jwk(), "crv": "P-256"}]}),
            "x curto": json.dumps({"keys": [{**self.par.jwk(), "x": "AAAA"}]}),
            "privada colada": json.dumps({"keys": [{**self.par.jwk(), "d": "segredo"}]}),
        }
        for nome, valor in casos.items():
            with self.subTest(nome), override_settings(CONECTA_ASSERCAO_CHAVES=valor):
                resposta = APIClient().get(QUEM_SOU, HTTP_X_CONECTA_ASSERCAO=self.par.assina())
                self.assertEqual(resposta.status_code, 503)
                self.assertEqual(resposta.json()["codigo"], "INTEGRACAO_DESLIGADA")

    @override_settings(
        DEBUG=False,
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "assercao": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "assercao"},
        },
    )
    def test_cache_por_processo_em_producao_desliga(self):
        """Com 3 workers e `LocMemCache`, o mesmo `jti` passaria 3 vezes (§4.6)."""
        with override_settings(CONECTA_ASSERCAO_CHAVES=conjunto(self.par)):
            resposta = APIClient().get(QUEM_SOU, HTTP_X_CONECTA_ASSERCAO=self.par.assina())
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json()["codigo"], "INTEGRACAO_DESLIGADA")

    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": "redis://x:1/3"}},
    )
    def test_redis_conta_como_compartilhado(self):
        self.assertTrue(autenticacao_conecta.cache_compartilhado())

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
    def test_dummy_cache_nunca_vale_nem_em_dev(self):
        """O `add` dele devolve sempre verdadeiro: toda repetição passaria."""
        self.assertFalse(autenticacao_conecta.cache_compartilhado())

    def test_cache_fora_do_ar_falha_fechado(self):
        quebrado = mock.Mock()
        quebrado.add.side_effect = ConnectionError("redis fora")
        with override_settings(CONECTA_ASSERCAO_CHAVES=conjunto(self.par)), mock.patch.object(
            autenticacao_conecta, "_cache_do_jti", return_value=quebrado
        ):
            resposta = APIClient().get(QUEM_SOU, HTTP_X_CONECTA_ASSERCAO=self.par.assina())
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json()["codigo"], "INTEGRACAO_DESLIGADA")

    @override_settings(
        DEBUG=False,
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": "redis://x:1/3"},
            "assercao": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        },
    )
    def test_quem_vale_e_o_alias_do_jti(self):
        """Redis só nos throttles não basta: o `jti` mora no alias `assercao`."""
        self.assertFalse(autenticacao_conecta.cache_compartilhado())

    @override_settings(
        DEBUG=False,
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "assercao": {"BACKEND": "config.cache.RedisQueNaoDerrubaAVenda", "LOCATION": "redis://x:1/3"},
        },
    )
    def test_cache_que_falha_aberto_nunca_guarda_o_jti(self):
        self.assertFalse(autenticacao_conecta.cache_compartilhado())


class CacheVazioTest(BaseAssercao):
    """Redis reiniciado volta vazio (não persiste, TSK-1068): a asserção que já
    foi usada, e ainda está no prazo, não pode passar de novo."""

    def esvazia(self):
        autenticacao_conecta._cache_do_jti().clear()

    def test_repeticao_depois_do_reinicio_nao_passa(self):
        token = self.par.assina()
        self.assertEqual(self.chama("get", QUEM_SOU, token).status_code, 200)
        self.esvazia()
        with self.assertLogs("lobby.integracao", level=logging.WARNING) as logs:
            resposta = self.chama("get", QUEM_SOU, token)
        self.assertRecusa(resposta, 503, "INTEGRACAO_DESLIGADA")
        self.assertIn("recém-esvaziado", "\n".join(logs.output))

    def test_fecha_so_ate_a_vida_maxima_de_uma_assercao(self):
        self.esvazia()
        self.assertRecusa(self.get(), 503, "INTEGRACAO_DESLIGADA")
        # O marco que a chamada acima gravou, envelhecido até o fim da janela:
        cache_do_jti = autenticacao_conecta._cache_do_jti()
        marco = cache_do_jti.get(autenticacao_conecta.MARCO_DO_CACHE)
        janela = autenticacao_conecta.janela_depois_de_cache_vazio()
        # Folga de 30 s para dentro: não depende de quanto o teste demora.
        cache_do_jti.set(autenticacao_conecta.MARCO_DO_CACHE, marco - janela + 30, None)
        self.assertRecusa(self.get(), 503, "INTEGRACAO_DESLIGADA")
        cache_do_jti.set(autenticacao_conecta.MARCO_DO_CACHE, int(time.time()) - janela - 1, None)
        self.assertEqual(self.get().status_code, 200)

    def test_o_503_da_janela_nao_queima_a_assercao(self):
        """O Conecta pode tentar de novo com o mesmo token depois da janela."""
        token = self.par.assina()
        self.esvazia()
        self.assertRecusa(self.chama("get", QUEM_SOU, token), 503, "INTEGRACAO_DESLIGADA")
        cache_contando_ha_tempo()
        self.assertEqual(self.chama("get", QUEM_SOU, token).status_code, 200)

    def repete_com_esvaziamento(self, quando):
        """Usa um token, e o repete com o cache esvaziado no meio da conferência:
        logo antes (`antes`) ou logo depois (`depois`) de gravar o `jti`."""
        token = self.par.assina()
        self.assertEqual(self.chama("get", QUEM_SOU, token).status_code, 200)
        real = autenticacao_conecta._cache_do_jti()
        proxy = mock.Mock(wraps=real)

        def add(chave, *args, **kwargs):
            eh_jti = chave.startswith("assercao:jti:lobby:")
            if eh_jti and quando == "antes":
                real.clear()
            feito = real.add(chave, *args, **kwargs)
            if eh_jti and quando == "depois":
                real.clear()
            return feito

        proxy.add.side_effect = add
        with mock.patch.object(autenticacao_conecta, "_cache_do_jti", return_value=proxy):
            return self.chama("get", QUEM_SOU, token)

    def test_esvaziar_logo_antes_de_gravar_o_jti_nao_deixa_repetir(self):
        self.assertRecusa(self.repete_com_esvaziamento("antes"), 503, "INTEGRACAO_DESLIGADA")

    def test_esvaziar_logo_depois_de_gravar_o_jti_nao_deixa_repetir(self):
        self.assertRecusa(self.repete_com_esvaziamento("depois"), 401, "JTI_REPETIDO")

    def test_marco_usa_o_relogio_da_gravacao_e_nao_o_do_inicio(self):
        """Worker que leu o relógio, ficou parado e só gravou o marco depois de
        um esvaziamento não pode gravar um marco de antes dele."""
        relogio = {"agora": float(int(time.time()))}
        real = autenticacao_conecta._cache_do_jti()
        proxy = mock.Mock(wraps=real)

        def add(chave, *args, **kwargs):
            if chave.startswith("assercao:jti:lobby:"):
                relogio["agora"] += 20  # parado entre ler o relógio e gravar
            return real.add(chave, *args, **kwargs)

        proxy.add.side_effect = add
        self.esvazia()
        with mock.patch.object(autenticacao_conecta, "_cache_do_jti", return_value=proxy), \
                mock.patch.object(autenticacao_conecta.time, "time", side_effect=lambda: relogio["agora"]):
            self.assertRecusa(self.get(), 503, "INTEGRACAO_DESLIGADA")
        self.assertGreaterEqual(real.get(autenticacao_conecta.MARCO_DO_CACHE), int(relogio["agora"]))

    def test_a_janela_cobre_a_vida_maxima_com_a_tolerancia_do_teto(self):
        """Aceita até exp+tol, exp até 70 s depois do iat, iat até tol no futuro
        — com o teto da tolerância, seja ele qual for."""
        from django.conf import settings as configuracao

        self.assertEqual(
            autenticacao_conecta.janela_depois_de_cache_vazio(),
            autenticacao_conecta.VALIDADE_MAXIMA + 2 * configuracao.CONECTA_ASSERCAO_TOLERANCIA_TETO,
        )
        self.assertLessEqual(configuracao.CONECTA_ASSERCAO_TOLERANCIA_SEGUNDOS, configuracao.CONECTA_ASSERCAO_TOLERANCIA_TETO)
        with override_settings(CONECTA_ASSERCAO_TOLERANCIA_TETO=60):
            self.assertEqual(
                autenticacao_conecta.janela_depois_de_cache_vazio(), autenticacao_conecta.VALIDADE_MAXIMA + 120
            )

    def test_o_jti_vive_ate_o_fim_do_ultimo_segundo_aceito(self):
        """Aceita enquanto int(agora) <= exp+tol: até exp+tol+1 de verdade."""
        cache_do_jti = mock.Mock(wraps=autenticacao_conecta._cache_do_jti())
        with mock.patch.object(autenticacao_conecta, "_cache_do_jti", return_value=cache_do_jti), \
                mock.patch.object(autenticacao_conecta.time, "time", return_value=1_000_000.5):
            exp = 1_000_000 + 60
            assercao = autenticacao_conecta.AssercaoConecta(
                kid="k", jti="j-borda", sub="s@x", aud="lobby", exp=exp, impersonacao=False
            )
            cache_contando_ha_tempo_em(cache_do_jti, 1_000_000)
            autenticacao_conecta.AutenticacaoConecta()._grava_jti(assercao)
        tolerancia = settings.CONECTA_ASSERCAO_TOLERANCIA_SEGUNDOS
        chamada = [c for c in cache_do_jti.add.call_args_list if "j-borda" in c.args[0]][0]
        self.assertEqual(chamada.kwargs["timeout"], exp + tolerancia + 1 - 1_000_000)


class LogTest(BaseAssercao):
    def test_a_assercao_nunca_vai_para_o_log(self):
        boa = self.par.assina()
        ruim = ParDeTeste(self.par.kid).assina()
        with self.assertLogs("lobby.integracao", level=logging.INFO) as logs:
            self.chama("get", QUEM_SOU, boa)
            self.chama("get", QUEM_SOU, ruim)
        texto = "\n".join(logs.output)
        for token in (boa, ruim):
            for pedaco in token.split("."):
                self.assertNotIn(pedaco, texto)

    def test_uma_linha_por_chamada_com_o_que_basta_para_depurar(self):
        token = self.par.assina()
        jti = jwt.decode(token, options={"verify_signature": False})["jti"]
        with self.assertLogs("lobby.integracao", level=logging.INFO) as logs:
            self.chama("get", QUEM_SOU, token)
        (linha,) = logs.output
        for trecho in (f"kid={self.par.kid}", f"jti={jti}", "sub=diretoria@sejaap.com.br", "aud=lobby",
                       "url=integracao-quem-sou", "metodo=GET", "status=200", "ip=127.0.0.1"):
            self.assertIn(trecho, linha)

    def test_recusa_de_relogio_registra_a_diferenca_medida(self):
        agora = int(time.time())
        with self.assertLogs("lobby.integracao", level=logging.WARNING) as logs:
            self.get(iat=agora - 125, nbf=agora - 125, exp=agora - 65)
        self.assertIn("codigo=ASSERCAO_EXPIRADA", logs.output[0])
        self.assertRegex(logs.output[0], r"venceu há 6\ds")

    def test_relatorio_de_erro_do_django_censura_o_cabecalho(self):
        from django.test import RequestFactory

        from config.erros import FiltroDeRelatorio

        request = RequestFactory().get("/", HTTP_X_CONECTA_ASSERCAO="segredo.de.verdade")
        meta = FiltroDeRelatorio().get_safe_request_meta(request)
        self.assertNotEqual(meta["HTTP_X_CONECTA_ASSERCAO"], "segredo.de.verdade")


class TetoTest(BaseAssercao):
    def test_recusas_seguidas_do_mesmo_ip_param_em_429(self):
        impostora = ParDeTeste(self.par.kid)
        with mock.patch.object(FalhasPorIp, "THROTTLE_RATES", {"integracao_falhas": "3/min"}):
            for _ in range(3):
                self.assertEqual(self.chama("get", QUEM_SOU, impostora.assina()).status_code, 401)
            resposta = self.chama("get", QUEM_SOU, self.par.assina())
        self.assertRecusa(resposta, 429, "MUITAS_CHAMADAS")

    def test_teto_de_leitura_e_por_pessoa(self):
        """Todas as chamadas saem do IP do Conecta: o teto é da pessoa."""
        with mock.patch.object(IntegracaoLeituraThrottle, "THROTTLE_RATES", {"integracao_leitura": "2/min"}):
            self.assertEqual(self.get().status_code, 200)
            self.assertEqual(self.get().status_code, 200)
            self.assertRecusa(self.get(), 429, "MUITAS_CHAMADAS")
            # Outra pessoa, mesmo IP: o balde é outro.
            self.assertEqual(self.get(sub="gerente@sejaap.com.br").status_code, 200)


class AchadosDaRevisaoTest(BaseAssercao):
    """O que a revisão de contexto limpo da TSK-1059 achou na autenticação."""

    def test_kid_desconhecido_nao_forja_linha_de_log(self):
        outra = ParDeTeste("x\n[ERROR] integracao kid=forjado status=200")
        with self.assertLogs("lobby.integracao", level=logging.INFO) as logs:
            self.chama("get", QUEM_SOU, outra.assina())
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("\n", logs.output[0])
        self.assertIn("kid='x\\n[ERROR]", logs.output[0])

    def test_sem_conta_nao_enche_o_teto_por_ip(self):
        """Sem conta vem com assinatura boa: é uma pessoa, não alguém batendo na porta."""
        with mock.patch.object(FalhasPorIp, "THROTTLE_RATES", {"integracao_falhas": "2/min"}):
            for _ in range(4):
                self.assertRecusa(self.get(sub="nunca.entrou@exemplo.com.br"), 401, "SEM_CONTA")
            self.assertEqual(self.get().status_code, 200)

    def test_conta_inativa_e_impersonacao_nao_enchem_o_teto_por_ip(self):
        self.gerente.is_active = False
        self.gerente.save()
        with mock.patch.object(FalhasPorIp, "THROTTLE_RATES", {"integracao_falhas": "2/min"}):
            for _ in range(3):
                self.assertRecusa(self.get(sub="gerente@sejaap.com.br"), 401, "CONTA_INATIVA")
                resposta = self.chama("put", "/api/cobranca", self.par.assina(imp=True), {"geral": {}})
                self.assertRecusa(resposta, 403, "IMPERSONACAO_RECUSADA")
            self.assertEqual(self.get().status_code, 200)

    def test_401_do_admin_antigo_segue_com_o_cabecalho_do_jwt(self):
        resposta = APIClient().put("/api/cobranca", {}, format="json")
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta["WWW-Authenticate"], 'Bearer realm="api"')

    @override_settings(
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "assercao": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": "redis://127.0.0.1:1/3",
                "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 1},
            },
        }
    )
    def test_redis_do_jti_fora_e_503_de_ponta_a_ponta(self):
        with self.assertLogs("lobby.integracao", level=logging.ERROR):
            resposta = self.get()
        self.assertRecusa(resposta, 503, "INTEGRACAO_DESLIGADA")


# O outro worker do gunicorn, num processo de verdade: grava o `jti` do token
# pelo mesmo caminho da autenticação e diz o que aconteceu.
_GRAVA_EM_OUTRO_PROCESSO = textwrap.dedent(
    """
    import sys

    import django

    django.setup()

    import jwt

    from apps.contas.autenticacao_conecta import AssercaoConecta, AssercaoRecusada, AutenticacaoConecta

    claims = jwt.decode(sys.argv[1], options={"verify_signature": False})
    assercao = AssercaoConecta(
        kid="outro-processo", jti=claims["jti"], sub=claims["sub"], aud=claims["aud"],
        exp=claims["exp"], impersonacao=False,
    )
    try:
        AutenticacaoConecta()._grava_jti(assercao)
    except AssercaoRecusada as recusa:
        print(recusa.codigo)
    else:
        print("GRAVOU")
    """
)


@skipUnless(
    os.environ.get("LOBBY_REDIS_URL"),
    "precisa de LOBBY_REDIS_URL apontando para um Redis DESCARTÁVEL (a suíte o "
    "esvazia); o CI sobe um — ver .github/workflows/ci.yml",
)
class JtiEntreProcessosTest(BaseAssercao):
    """A razão de o Lobby ter um Redis (TSK-1068): o mesmo `jti` em dois workers.

    O `test_jti_repetido` repete no MESMO processo, e passaria com o
    `LocMemCache`. Aqui o outro worker é outro processo Python, com o settings
    de verdade e o `LOBBY_REDIS_URL` do ambiente — se o cache não fosse
    compartilhado, ele gravaria o `jti` de novo sem saber da primeira chamada.

    Pulado sem Redis, e o CI reprova teste pulado: lá ele sempre roda.
    """

    def outro_processo(self, token):
        ambiente = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"}
        feito = subprocess.run(
            [sys.executable, "-c", _GRAVA_EM_OUTRO_PROCESSO, token],
            cwd=settings.BASE_DIR, env=ambiente, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(feito.returncode, 0, feito.stderr)
        linhas = feito.stdout.strip().splitlines()
        self.assertTrue(linhas, f"o outro processo não respondeu nada: {feito.stderr}")
        return linhas[-1]

    def test_o_settings_usa_o_redis(self):
        """Sem isto, os dois abaixo poderiam passar por outro motivo."""
        self.assertEqual(settings.CACHES["assercao"]["BACKEND"], "django.core.cache.backends.redis.RedisCache")
        self.assertTrue(autenticacao_conecta.cache_compartilhado())

    def test_outro_worker_usou_primeiro(self):
        token = self.par.assina()
        self.assertEqual(self.outro_processo(token), "GRAVOU")
        self.assertRecusa(self.chama("get", QUEM_SOU, token), 401, "JTI_REPETIDO")

    def test_este_worker_usou_primeiro(self):
        token = self.par.assina()
        self.assertEqual(self.chama("get", QUEM_SOU, token).status_code, 200)
        self.assertEqual(self.outro_processo(token), "JTI_REPETIDO")
