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
    ErroIdentidade,
    IdentidadeIndisponivel,
    NaoEncontrado,
    SemAcessoAoApp,
    SenhaFraca,
    TokenInvalido,
    central_ativa,
)

from .identidade import resolver_usuario
from .models import papel_de
from .throttling import DefinicaoSenhaThrottle, LoginThrottle

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
MSG_LINK_MORTO = (
    "Este link não vale mais. Ele expira em 48 horas e só pode ser usado uma "
    "vez — peça um novo na tela de esqueci minha senha."
)
# A mesma frase para todo endereço. Ver `esqueci_senha`.
MSG_LINK_PEDIDO = (
    "Se houver uma conta com esse e-mail, o link para definir a senha acabou "
    "de ser enviado. Ele vale 48 horas e serve uma vez só."
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


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([DefinicaoSenhaThrottle])
def definir_senha(request):
    """POST /api/senha/definir — {token, senha_nova}.

    O outro lado do link que o admin do Conecta ID gera: o administrador
    entrega o link, e é aqui que a pessoa escolhe a própria senha. Nem o
    administrador conhece o resultado.

    Existe porque, sem ela, o Lobby era o único app da empresa em que a senha
    do Conecta ID não podia ser definida — o aviso de troca mandava o gerente
    pedir o link no kanban, um sistema em que boa parte de quem vende não
    entra. O login já era 100% central; faltava esta metade do ciclo.

    Anônima de propósito: quem chega aqui ainda não consegue entrar. E sem eco
    no erro do token — inexistente, expirado e já usado devolvem a mesma frase,
    porque diferenciar contaria a quem chuta que aquele formato de token
    existe. Quem valida e grava é o Conecta ID; daqui não passa nada perto de
    banco de senha.
    """
    if not central_ativa():
        # Mesmo desfecho do login: sem a central não há senha nenhuma para
        # definir, e falhar alto evita a pessoa achar que definiu.
        logger.error("AUTH_CENTRAL_ATIVO desligado — definição de senha indisponível")
        return _resposta(MSG_INDISPONIVEL, status.HTTP_503_SERVICE_UNAVAILABLE)

    dados_entrada = request.data or {}
    token = (dados_entrada.get("token") or "").strip()
    senha = dados_entrada.get("senha_nova") or ""
    if not token or not senha:
        return _resposta(
            "Informe o link completo e a senha nova.", status.HTTP_400_BAD_REQUEST
        )

    try:
        ClienteIdentidade().definir_senha(token, senha)
    except TokenInvalido:
        return _resposta(MSG_LINK_MORTO, status.HTTP_400_BAD_REQUEST)
    except SenhaFraca as erro:
        # Aqui a mensagem detalhada É útil, ao contrário do erro de token: é a
        # pessoa escolhendo a senha dela, e ela precisa saber o que corrigir.
        return _resposta(str(erro), status.HTTP_400_BAD_REQUEST)
    except IdentidadeIndisponivel:
        logger.exception("Conecta ID indisponível na definição de senha")
        return _resposta(MSG_INDISPONIVEL, status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response({"ok": True, "mensagem": "Senha definida. Você já pode entrar."})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([DefinicaoSenhaThrottle])
def esqueci_senha(request):
    """POST /api/senha/esqueci — {email}.

    A metade que faltava do ciclo. `definir_senha`, acima, resolveu o "como a
    pessoa escolhe a senha"; o "como ela consegue o link" continuava sendo
    pedir a um administrador, que gerava no kanban — um sistema em que boa
    parte de quem vende não entra. Agora o link sai daqui, direto para a caixa
    de quem pediu.

    **A resposta é a mesma para qualquer e-mail**, e é o ponto da view: esta é
    a rota mais exposta do Lobby depois do login, e dizer "não há conta com
    esse endereço" entregaria a um estranho a lista de quem trabalha na
    empresa, um palpite por vez.

    O teto por hora é o mesmo da definição de senha, e o Conecta ID tem o dele,
    contado por e-mail. Os dois medem coisas diferentes: aqui, quanto um IP
    pode martelar o Lobby; lá, quantas mensagens uma caixa pode receber.
    """
    if not central_ativa():
        logger.error("AUTH_CENTRAL_ATIVO desligado — pedido de link indisponível")
        return _resposta(MSG_INDISPONIVEL, status.HTTP_503_SERVICE_UNAVAILABLE)

    email = ((request.data or {}).get("email") or "").strip()
    if not email:
        return _resposta("Informe o seu e-mail.", status.HTTP_400_BAD_REQUEST)

    try:
        ClienteIdentidade().esqueci_senha(email)
    except ErroIdentidade:
        # Larga de propósito: nesta rota o Conecta ID não devolve erro sobre a
        # conta — responde 202 para qualquer endereço. Tudo que chega aqui é
        # infraestrutura, e nenhum desses casos pode virar resposta diferente
        # por e-mail.
        logger.exception("falha ao pedir o link de senha ao Conecta ID")
        return _resposta(MSG_INDISPONIVEL, status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response({"ok": True, "mensagem": MSG_LINK_PEDIDO})
