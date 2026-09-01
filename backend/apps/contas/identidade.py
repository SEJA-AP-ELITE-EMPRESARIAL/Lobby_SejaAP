"""
Como uma identidade do Conecta ID vira um usuário do Lobby.

Este módulo é apontado por `IDENTIDADE_RESOLVER_USUARIO` no settings, e é
chamado pelo `BackendIdentidade` logo depois de a senha conferir.
"""
import logging

from django.contrib.auth import get_user_model

from identidade_client import Provisionamento, resolver_com_vinculo

from .models import Papel, VinculoIdentidade

logger = logging.getLogger(__name__)


def _reaplicar_provisionamento(usuario, dados):
    """Força o papel do Conecta ID por cima de um vínculo que já existe.

    Só acontece quando alguém pediu, à mão, no admin do Conecta ID, e vale uma
    vez. É a exceção à regra de que a configuração é semente — sem ela, definir
    o papel de quem já entrou no Lobby não teria efeito nenhum, e a promoção
    continuaria sendo uma segunda ida ao /django-admin/.

    Papel vazio ou desconhecido não rebaixa ninguém: o pedido não diz "tire o
    papel", diz "faça valer o que está escrito", e não há nada escrito. Tirar um
    papel continua sendo operação do /django-admin/, onde quem faz vê o que está
    fazendo.
    """
    papel = Provisionamento(dados).escolha("papel", set(Papel.values), "")
    if not papel:
        logger.info(
            "reaplicação sem papel definido para %s; nada a fazer", usuario.email
        )
        return

    vinculo = getattr(usuario, "vinculo_identidade", None)
    if vinculo is None or vinculo.papel == papel:
        return

    anterior = vinculo.papel or "sem papel"
    vinculo.papel = papel
    # `save()` inteiro, não `update_fields`: é ele que amarra `is_staff` ao
    # papel. Um update parcial promoveria alguém a diretoria sem lhe dar o
    # /django-admin/, que é o que aquela amarração existe para evitar.
    vinculo.save()
    logger.info(
        "papel de %s reaplicado pelo Conecta ID: %s -> %s", usuario.email, anterior, papel
    )


def _semear_papel(usuario, dados):
    """Grava o papel que veio do Conecta ID, no primeiro acesso e só nele.

    Era a metade que faltava: conceder o Lobby no Conecta ID entregava uma conta
    que entra e não autoriza nada, e alguém tinha de ir ao /django-admin/
    promover. Quem concede agora diz, no mesmo lugar, o que a pessoa é aqui.

    Papel desconhecido não vira erro: fica vazio, que é o mesmo estado de quem
    ainda não foi promovido, e o log diz por quê. Recusar a entrada por causa de
    uma palavra digitada errada seria caro para quem não errou nada.
    """
    papel = Provisionamento(dados).escolha("papel", set(Papel.values), "")
    if not papel:
        return

    # Pelo cache da relação, não por consulta nova. `resolver_com_vinculo`
    # acabou de criar o vínculo e deixou o objeto preso em `usuario`; gravar
    # numa CÓPIA vinda do banco deixaria esse objeto com o papel velho — e é
    # justamente ele que a view lê logo em seguida para decidir se a pessoa
    # entra. O papel iria para o banco e a mesma requisição responderia 403.
    vinculo = getattr(usuario, "vinculo_identidade", None)
    if vinculo is None or vinculo.papel:
        return
    vinculo.papel = papel
    # `save()` inteiro, não `update_fields`: o `save` do modelo é quem amarra
    # `is_staff` ao papel (só a diretoria entra no admin), e um update parcial
    # passaria por fora dele.
    vinculo.save()
    logger.info("papel %s semeado para %s pelo Conecta ID", papel, usuario.email)


def _criar_usuario_local(dados):
    """Cria a conta local de quem nunca entrou aqui.

    Nasce SEM papel e SEM staff. É deliberado: ter acesso ao Lobby no Conecta ID
    significa "pode entrar", não "pode autorizar desconto". A diretoria promove
    depois, no /django-admin/ ou pelo comando `promover_no_lobby`.

    Devolver `None` aqui seria o outro caminho — recusar a entrada de quem não
    foi promovido antes. Não foi o escolhido, porque o Conecta ID só libera o app
    para quem a empresa já decidiu que deve entrar, e recusar produziria um erro
    indistinguível de senha errada bem no primeiro acesso da pessoa.
    """
    Usuario = get_user_model()
    email = (dados.get("email") or "").strip().lower()
    nome = (dados.get("nome") or "").strip()
    partes = nome.split()

    usuario = Usuario.objects.create(
        # O `username` do Django é obrigatório e único; o login de verdade é o
        # e-mail, que é o que o Conecta ID conhece.
        username=email or str(dados["identidade_id"]),
        email=email,
        first_name=partes[0] if partes else "",
        last_name=" ".join(partes[1:]) if len(partes) > 1 else "",
        is_active=True,
        is_staff=False,
    )
    # Sem senha utilizável: quem guarda senha é o Conecta ID. Uma senha local
    # aqui seria uma segunda porta para a mesma conta, fora da política central.
    usuario.set_unusable_password()
    usuario.save(update_fields=["password"])

    logger.info("conta local criada no Lobby para %s (sem papel)", email)
    return usuario


def resolver_usuario(dados):
    """Ponte entre o Conecta ID e o usuário local. Devolve `User` ou `None`."""
    # Perguntado ANTES de resolver: `resolver_com_vinculo` cria o vínculo, e
    # depois não dá mais para saber se ele nasceu agora. O papel só é semeado
    # quando nasce — reaplicá-lo a cada login desfaria, sem avisar, uma
    # promoção feita no /django-admin/.
    primeiro_acesso = not VinculoIdentidade.objects.filter(
        identidade_id=dados["identidade_id"]
    ).exists()

    usuario = resolver_com_vinculo(
        dados,
        VinculoIdentidade,
        ao_criar=_criar_usuario_local,
        ao_reaplicar=_reaplicar_provisionamento,
    )
    if usuario is None:
        return None

    if primeiro_acesso:
        _semear_papel(usuario, dados)

    # `precisa_trocar_senha` é do Conecta ID e muda a cada login; guardar a
    # cópia local permite que a tela avise sem precisar de outra chamada.
    vinculo = VinculoIdentidade.objects.filter(usuario=usuario).first()
    if vinculo is not None:
        precisa = bool(dados.get("precisa_trocar_senha", False))
        if vinculo.precisa_trocar_senha != precisa:
            vinculo.precisa_trocar_senha = precisa
            vinculo.save(update_fields=["precisa_trocar_senha", "atualizado_em"])

    return usuario
