"""
Cliente do Conecta ID — o módulo que cada app instala.

FONTE DE VERDADE: `conecta-id/cliente/identidade_client.py`. Nos apps ele é
copiado, não editado. Corrigiu algo? Corrija aqui e recopie — um cliente que
diverge entre apps é a forma mais silenciosa de dois sistemas passarem a tratar
o mesmo erro de jeitos diferentes.

Só depende de Django e da biblioteca padrão. Nada de `requests`: são cinco
chamadas HTTP simples, e uma dependência a mais em três apps custa mais do que
as vinte linhas de `urllib` que ela pouparia.

## Como um app usa

    # settings.py
    AUTHENTICATION_BACKENDS = [
        "identidade_client.BackendIdentidade",
        "django.contrib.auth.backends.ModelBackend",   # fallback local
    ]
    IDENTIDADE_URL = "http://identidade-api:8000"
    IDENTIDADE_APP_KEY = "AppKey financeiro:..."       # do .env
    AUTH_CENTRAL_ATIVO = True                          # a chave que reverte tudo
    IDENTIDADE_RESOLVER_USUARIO = "apps.formularios.identidade.resolver_usuario"

O `resolver_usuario` é do app, e é onde mora tudo que o Conecta ID não sabe:
criar o Perfil no financeiro, o nível de acesso e a equipe no kanban. O cliente
entrega quem é a pessoa; o app decide o que ela é lá dentro.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.utils.module_loading import import_string

logger = logging.getLogger("identidade_client")

VERSAO = "1.0.0"


# === Erros ================================================================
class ErroIdentidade(Exception):
    """Base. O app pode capturar só esta para tratar tudo de uma vez."""


class CredencialInvalida(ErroIdentidade):
    """E-mail ou senha incorretos — ou conta inativa, ou sem senha definida.

    O serviço não distingue os quatro casos de propósito, e o cliente também
    não deve tentar adivinhar: repassar a mensagem genérica é o comportamento
    correto.
    """


class SemAcessoAoApp(ErroIdentidade):
    """A senha confere, mas esta conta não tem acesso a este sistema."""


class BloqueadoTemporariamente(ErroIdentidade):
    """Excesso de tentativas. Mensagem para o usuário: tente mais tarde."""


class SenhaFraca(ErroIdentidade):
    """A senha nova não passou na política. `detalhe` explica o quê."""


class TokenInvalido(ErroIdentidade):
    """Link de senha inexistente, expirado ou já usado."""


class EmailEmUso(ErroIdentidade):
    def __init__(self, mensagem, identidade_id=None):
        super().__init__(mensagem)
        self.identidade_id = identidade_id


class NaoEncontrado(ErroIdentidade):
    """Não há identidade com esse e-mail liberada para este app.

    Erro próprio, e não `IdentidadeIndisponivel`: é uma resposta legítima do
    serviço sobre os dados enviados, não uma falha de infraestrutura. Tratar os
    dois como a mesma coisa faria a tela dizer "tente novamente em instantes"
    para quem digitou um e-mail que não existe — e a pessoa tentaria de novo,
    para sempre.
    """


class IdentidadeIndisponivel(ErroIdentidade):
    """O Conecta ID não respondeu, ou respondeu 5xx.

    **Este erro nunca pode virar "senha incorreta" na tela.** Se o serviço cai
    e todo mundo vê "senha incorreta" ao mesmo tempo, a leitura natural é
    vazamento de credenciais — e vem uma enxurrada de trocas de senha que não
    resolve nada. A tela certa é "sistema de login indisponível, tente em
    instantes".
    """


_ERROS = {
    "credencial_invalida": CredencialInvalida,
    "sem_acesso_ao_app": SemAcessoAoApp,
    "bloqueado_temporariamente": BloqueadoTemporariamente,
    "senha_fraca": SenhaFraca,
    "token_invalido": TokenInvalido,
    "nao_encontrado": NaoEncontrado,
    "indisponivel": IdentidadeIndisponivel,
}


# === Cliente HTTP =========================================================
class ClienteIdentidade:
    def __init__(self, url=None, chave=None, timeout=None):
        self.url = (url or getattr(settings, "IDENTIDADE_URL", "")).rstrip("/")
        self.chave = chave or getattr(settings, "IDENTIDADE_APP_KEY", "")
        # 5s é generoso: a chamada mais cara é um Argon2, na casa dos 60ms. Um
        # timeout largo aqui viraria fila de gunicorn presa esperando um
        # serviço que já caiu.
        self.timeout = timeout or getattr(settings, "IDENTIDADE_TIMEOUT", 5)

    # --- chamadas ---------------------------------------------------------
    def verificar(self, login, senha, ip=None):
        """Devolve os dados da identidade ou levanta o erro correspondente."""
        corpo = {"login": login, "senha": senha}
        if ip:
            corpo["ip"] = ip
        return self._pedir("POST", "/api/v1/credenciais/verificar", corpo)

    def buscar_por_email(self, email):
        achados = self._pedir(
            "GET", "/api/v1/identidades?email=" + urllib.parse.quote(email)
        )
        return achados[0] if achados else None

    def obter(self, identidade_id):
        return self._pedir("GET", f"/api/v1/identidades/{identidade_id}")

    def criar(self, email, nome, apps=(), enviar_convite=False, senha_inicial=None):
        corpo = {
            "email": email,
            "nome": nome,
            "apps": list(apps),
            "enviar_convite": enviar_convite,
        }
        if senha_inicial:
            # Senha provisória combinada. A identidade nasce marcada para troca
            # obrigatória — uma senha que o admin conhece só vale até o
            # primeiro acesso.
            corpo["senha_inicial"] = senha_inicial
        return self._pedir("POST", "/api/v1/identidades", corpo)

    def alterar(self, identidade_id, **campos):
        return self._pedir("PATCH", f"/api/v1/identidades/{identidade_id}", campos)

    def conceder_acesso(self, identidade_id, app):
        self._pedir("POST", "/api/v1/acessos", {"identidade_id": str(identidade_id), "app": app})

    def revogar_acesso(self, identidade_id, app):
        self._pedir("DELETE", "/api/v1/acessos", {"identidade_id": str(identidade_id), "app": app})

    def emitir_token_de_senha(self, email):
        resposta = self._pedir("POST", "/api/v1/senha/token", {"email": email})
        return (resposta or {}).get("token")

    def definir_senha(self, token, senha_nova):
        self._pedir("POST", "/api/v1/senha/definir", {"token": token, "senha_nova": senha_nova})

    def redefinir_sem_token(self, login, senha_nova, ip=None):
        """Autoatendimento: redefine sem senha atual e sem token.

        Só existe porque o serviço permite — e ele só permite para quem tem
        acesso ao app que está chamando. Ver a rotina do mesmo nome no Conecta
        ID para a decisão e o que ficou no lugar da prova de posse.
        """
        corpo = {"login": login, "senha_nova": senha_nova}
        if ip:
            corpo["ip"] = ip
        self._pedir("POST", "/api/v1/senha/redefinir", corpo)

    def trocar_senha(self, identidade_id, senha_atual, senha_nova, ip=None):
        corpo = {"senha_atual": senha_atual, "senha_nova": senha_nova}
        if ip:
            corpo["ip"] = ip
        self._pedir("POST", f"/api/v1/identidades/{identidade_id}/senha", corpo)

    def saudavel(self):
        """Para healthcheck do app: True se o Conecta ID responde."""
        try:
            self._pedir("GET", "/health", autenticado=False)
            return True
        except ErroIdentidade:
            return False

    # --- transporte -------------------------------------------------------
    def _pedir(self, metodo, caminho, corpo=None, autenticado=True):
        if not self.url:
            raise IdentidadeIndisponivel("IDENTIDADE_URL não configurada.")

        dados = json.dumps(corpo).encode() if corpo is not None else None
        requisicao = urllib.request.Request(self.url + caminho, data=dados, method=metodo)
        requisicao.add_header("Content-Type", "application/json")
        if autenticado:
            if not self.chave:
                raise IdentidadeIndisponivel("IDENTIDADE_APP_KEY não configurada.")
            requisicao.add_header("Authorization", self.chave)

        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                texto = resposta.read().decode()
                return json.loads(texto) if texto else None
        except urllib.error.HTTPError as erro:
            self._traduzir(erro)
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            # Rede caiu, DNS não resolveu, timeout. Nunca confundir com senha
            # errada: quem decide o que mostrar é o app, e ele precisa saber a
            # diferença.
            logger.error("Conecta ID inalcançável em %s%s: %s", self.url, caminho, erro)
            raise IdentidadeIndisponivel("Serviço de identidade inalcançável.") from erro

    def _traduzir(self, erro_http):
        try:
            corpo = json.loads(erro_http.read().decode())
        except Exception:
            corpo = {}
        codigo = corpo.get("erro", "")
        detalhe = corpo.get("detalhe", "Falha ao falar com o serviço de identidade.")

        if codigo == "email_em_uso":
            raise EmailEmUso(detalhe, corpo.get("identidade_id"))

        classe = _ERROS.get(codigo)
        if classe:
            raise classe(detalhe)

        # `app_nao_autorizado`, `sem_permissao`, `nao_encontrado`,
        # `dados_invalidos` e qualquer 5xx caem aqui. São erros de integração,
        # não do usuário: quem errou foi o app, e o log precisa dizer isso alto.
        logger.error(
            "Conecta ID recusou a chamada (HTTP %s, erro=%s): %s",
            erro_http.code, codigo or "?", detalhe,
        )
        raise IdentidadeIndisponivel(detalhe)


# === Backend de autenticação do Django ====================================
def central_ativa():
    """A chave que reverte tudo, sem migration e sem deploy de emergência."""
    return bool(getattr(settings, "AUTH_CENTRAL_ATIVO", False))


class BackendIdentidade(BaseBackend):
    """Autentica no Conecta ID e devolve o usuário local correspondente.

    Fica **antes** do `ModelBackend` na `AUTHENTICATION_BACKENDS`. Com
    `AUTH_CENTRAL_ATIVO=False` ele sai da frente sem fazer nada, e o login
    volta a ser o local — é assim que se desliga a integração em segundos.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not central_ativa() or not username or not password:
            return None

        cliente = ClienteIdentidade()
        try:
            dados = cliente.verificar(username, password, ip=self._ip(request))
        except (CredencialInvalida, SemAcessoAoApp):
            # `None` faz o Django seguir para o próximo backend e, no fim,
            # responder "credenciais inválidas" — que é o certo aqui.
            return None
        # BloqueadoTemporariamente e IdentidadeIndisponivel sobem de propósito:
        # a view precisa distinguir "tente mais tarde" de "senha errada".

        resolver = import_string(settings.IDENTIDADE_RESOLVER_USUARIO)
        usuario = resolver(dados)
        if usuario is None:
            logger.error(
                "identidade %s autenticou mas o app não resolveu o usuário local",
                dados.get("identidade_id"),
            )
            return None

        # `precisa_trocar_senha` vem do Conecta ID; a view do app decide o que
        # fazer com ele (banner, redirecionamento).
        usuario.identidade_precisa_trocar_senha = dados.get("precisa_trocar_senha", False)
        return usuario

    def get_user(self, user_id):
        from django.contrib.auth import get_user_model

        try:
            return get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            return None

    @staticmethod
    def _ip(request):
        """IP do usuário final, para a auditoria e o bloqueio por origem.

        Atrás do nginx do host, o que vale é o X-Forwarded-For; o REMOTE_ADDR
        seria sempre o do proxy, e o bloqueio por IP viraria bloqueio geral.
        """
        if request is None:
            return None
        encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if encaminhado:
            return encaminhado.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


# === Usuário sombra =======================================================
def resolver_com_vinculo(dados, modelo_vinculo, ao_criar=None, sincronizar=True):
    """Devolve o usuário local correspondente à identidade, criando se preciso.

    `modelo_vinculo` é um modelo do PRÓPRIO app, com `identidade_id` (UUID
    único) e `usuario` (OneToOne). Cinco linhas, e é o app que as declara:

        class VinculoIdentidade(models.Model):
            identidade_id = models.UUIDField(unique=True, db_index=True)
            usuario = models.OneToOneField(
                settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                related_name="vinculo_identidade")

    O casamento é por `identidade_id` primeiro e por e-mail depois. A ordem
    importa: o e-mail muda (casamento, troca de sobrenome, correção de digitação
    — o Diego que o diga), o UUID não. Casar por e-mail primeiro faria uma
    correção de cadastro criar um usuário novo e órfão, com o histórico do
    antigo preso na conta velha.

    `ao_criar(dados)` é chamado quando não existe usuário local nenhum. É lá
    que o app decide o que a pessoa é dentro dele — papel no financeiro, nível
    de acesso e equipe no kanban. Se devolver `None`, ninguém entra: é o jeito
    de dizer "esta pessoa tem identidade, mas não tem lugar aqui".
    """
    identidade_id = dados["identidade_id"]
    email = (dados.get("email") or "").strip().lower()

    vinculo = modelo_vinculo.objects.filter(identidade_id=identidade_id).select_related(
        "usuario"
    ).first()
    if vinculo:
        usuario = vinculo.usuario
        if sincronizar:
            _sincronizar(usuario, dados)
        return usuario

    Usuario = modelo_vinculo._meta.get_field("usuario").related_model
    usuario = Usuario.objects.filter(email__iexact=email).first() if email else None

    if usuario is None:
        if ao_criar is None:
            return None
        usuario = ao_criar(dados)
        if usuario is None:
            return None

    # `get_or_create` e não `create`: se dois requests da mesma pessoa chegarem
    # juntos no primeiro login, os dois tentam vincular ao mesmo usuário.
    modelo_vinculo.objects.get_or_create(
        usuario=usuario, defaults={"identidade_id": identidade_id}
    )
    if sincronizar:
        _sincronizar(usuario, dados)
    return usuario


def _sincronizar(usuario, dados):
    """Traz e-mail e nome do Conecta ID para o usuário local.

    O Conecta ID é a fonte para estes dois campos; o resto (papel, equipe,
    permissões) é do app e não se toca. Sem esta sincronização, corrigir um
    e-mail no lugar certo não teria efeito nenhum onde as pessoas olham.
    """
    campos = []
    email = (dados.get("email") or "").strip().lower()
    if email and getattr(usuario, "email", "") != email:
        usuario.email = email
        campos.append("email")

    nome = (dados.get("nome") or "").strip()
    if nome and hasattr(usuario, "nome") and usuario.nome != nome:
        usuario.nome = nome
        campos.append("nome")
    elif nome and hasattr(usuario, "first_name"):
        partes = nome.split()
        primeiro, ultimo = partes[0], " ".join(partes[1:])
        if usuario.first_name != primeiro or usuario.last_name != ultimo:
            usuario.first_name, usuario.last_name = primeiro, ultimo
            campos += ["first_name", "last_name"]

    if campos:
        usuario.save(update_fields=campos)
