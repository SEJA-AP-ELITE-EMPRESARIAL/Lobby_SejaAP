"""
Login do Lobby — `POST /api/sessao`.

O caminho e o formato da resposta são os que o front já usa
(`index.html:196-206` e `admin.html:113-121`), com uma diferença: o corpo agora
é `{email, senha}` em vez de `{senha}`. Era senha única compartilhada; passa a
ser a credencial de cada pessoa no Conecta ID.

    POST /api/sessao   {"email": "...", "senha": "..."}
      200 { token, exp, nome, email, papel, podeAutorizarNegociacao,
            podePublicarTabela, precisaTrocarSenha }
      401 { erro }   credencial inválida
      403 { erro }   entrou, mas não pode fazer o que pediu
      429 { erro }   bloqueado por tentativas
      503 { erro }   Conecta ID fora do ar

`exp` é unix em SEGUNDOS, porque o front faz `raw.exp * 1000 > Date.now()`
(`index.html:182`).

POR QUE ESTA VIEW NÃO USA `authenticate()`

O `BackendIdentidade` converte tanto `CredencialInvalida` quanto
`SemAcessoAoApp` em `None` (`identidade_client.py:268-271`), e a view fica sem
como distinguir os dois. Para o /django-admin/ isso é aceitável. Aqui não é:
um gerente que existe no Conecta ID mas ainda não recebeu acesso ao app `lobby`
digitaria a senha certa e leria "e-mail ou senha incorretos" — e o desfecho
previsível é a pessoa redefinir a senha, falhar de novo e abrir chamado.

Como o Conecta ID só devolve `sem_acesso_ao_app` DEPOIS de a senha conferir
(`conecta-id/apps/identidade/servicos.py:82-94`), dizer isso em voz alta não
entrega nada a quem não tem a credencial. O `BackendIdentidade` continua
configurado em `AUTHENTICATION_BACKENDS` e é ele quem atende o /django-admin/.
"""
import logging

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from identidade_client import (
    BloqueadoTemporariamente,
    ClienteIdentidade,
    CredencialInvalida,
    IdentidadeIndisponivel,
    NaoEncontrado,
    SemAcessoAoApp,
    central_ativa,
)

from .identidade import resolver_usuario
from .models import papel_de
from .throttling import LoginThrottle

logger = logging.getLogger(__name__)

MSG_CREDENCIAL = "E-mail ou senha incorretos."
MSG_SEM_ACESSO = "Sua conta não tem acesso ao Lobby. Fale com a diretoria."
MSG_SEM_PAPEL = (
    "Sua conta ainda não tem permissão para autorizar negociação. "
    "Peça à diretoria para liberar."
)
MSG_BLOQUEADO = "Muitas tentativas. Aguarde alguns minutos e tente de novo."
MSG_INDISPONIVEL = (
    "O sistema de login está indisponível no momento. Tente em instantes."
)


def _ip_do_usuario(request):
    """O IP de quem digitou, não o do container.

    O Conecta ID usa este IP para bloquear força bruta por origem. Sem
    repassá-lo, todas as tentativas chegam lá com o mesmo IP — o do backend — e
    o bloqueio por origem vira bloqueio geral: um consultor errando a senha
    trancaria a empresa inteira.
    """
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _resposta(mensagem, codigo):
    return Response({"erro": mensagem}, status=codigo)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def abrir_sessao(request):
    """Troca e-mail + senha por um token de sessão."""
    if not central_ativa():
        # Não é rollback: com a central desligada ninguém entra, porque não há
        # senha local nenhuma neste app. Falhar alto é melhor que devolver 401
        # e mandar todo mundo caçar a senha errada.
        logger.error("AUTH_CENTRAL_ATIVO está desligado — nenhum login é possível")
        return _resposta(MSG_INDISPONIVEL, status.HTTP_503_SERVICE_UNAVAILABLE)

    dados_entrada = request.data or {}
    email = (dados_entrada.get("email") or "").strip().lower()
    senha = dados_entrada.get("senha") or ""
    if not email or not senha:
        return _resposta("Informe o e-mail e a senha.", status.HTTP_400_BAD_REQUEST)

    try:
        identidade = ClienteIdentidade().verificar(
            email, senha, ip=_ip_do_usuario(request)
        )
    except (CredencialInvalida, NaoEncontrado):
        return _resposta(MSG_CREDENCIAL, status.HTTP_401_UNAUTHORIZED)
    except SemAcessoAoApp:
        return _resposta(MSG_SEM_ACESSO, status.HTTP_403_FORBIDDEN)
    except BloqueadoTemporariamente:
        return _resposta(MSG_BLOQUEADO, status.HTTP_429_TOO_MANY_REQUESTS)
    except IdentidadeIndisponivel:
        # NUNCA vira "senha incorreta". Se o serviço cai e todo mundo lê "senha
        # incorreta" ao mesmo tempo, a leitura natural é vazamento — e vem uma
        # enxurrada de trocas de senha que não resolve nada.
        logger.exception("Conecta ID indisponível no login")
        return _resposta(MSG_INDISPONIVEL, status.HTTP_503_SERVICE_UNAVAILABLE)

    usuario = resolver_usuario(identidade)
    if usuario is None or not usuario.is_active:
        logger.error(
            "identidade %s autenticou mas não virou usuário local",
            identidade.get("identidade_id"),
        )
        return _resposta(MSG_CREDENCIAL, status.HTTP_401_UNAUTHORIZED)

    vinculo = usuario.vinculo_identidade
    if not vinculo.pode_autorizar_negociacao:
        # Entrou, mas não pode nada. 403 e não 401, porque 401 derruba a sessão
        # do admin (`admin.html:228`) e a mensagem precisa ser vista.
        return _resposta(MSG_SEM_PAPEL, status.HTTP_403_FORBIDDEN)

    token = RefreshToken.for_user(usuario).access_token
    return Response(
        {
            "token": str(token),
            # Segundos, não milissegundos — `index.html:182` multiplica por 1000.
            "exp": int(token["exp"]),
            "nome": usuario.get_full_name() or usuario.email,
            "email": usuario.email,
            "papel": vinculo.papel,
            "podeAutorizarNegociacao": vinculo.pode_autorizar_negociacao,
            "podePublicarTabela": vinculo.pode_publicar_tabela,
            "precisaTrocarSenha": vinculo.precisa_trocar_senha,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sessao_atual(request):
    """Quem está logado — para a tela mostrar o nome e o botão de sair."""
    vinculo = getattr(request.user, "vinculo_identidade", None)
    return Response(
        {
            "nome": request.user.get_full_name() or request.user.email,
            "email": request.user.email,
            "papel": papel_de(request.user),
            "podeAutorizarNegociacao": bool(
                vinculo and vinculo.pode_autorizar_negociacao
            ),
            "podePublicarTabela": bool(vinculo and vinculo.pode_publicar_tabela),
            "precisaTrocarSenha": bool(vinculo and vinculo.precisa_trocar_senha),
        }
    )
