"""
Quem é quem dentro do Lobby.

O Conecta ID responde "esta pessoa é quem diz ser, e pode entrar neste app".
Ele NÃO responde "o que ela pode fazer aqui" — de propósito: papel, equipe e
permissão de negócio ficam no banco de cada app
(`conecta-id/apps/identidade/models/identidade.py:1-9`).

O `AcessoAplicacao` de lá é binário: tem acesso ao Lobby ou não tem. Como aqui
existem dois níveis distintos — gerente autoriza negociação, diretoria também
mexe na tabela de preços — o papel precisa morar deste lado. É este arquivo.
"""
from django.conf import settings
from django.db import models


class Papel(models.TextChoices):
    """Os dois níveis do Lobby.

    Ninguém nasce com papel. Quem entra pela primeira vez fica sem nenhum e não
    autoriza nada até a diretoria promover — ver `resolver_usuario`. Herdar
    permissão por padrão é o tipo de default que vira incidente.
    """

    GERENTE = "gerente", "Gerente — autoriza negociação"
    DIRETORIA = "diretoria", "Diretoria — autoriza negociação e publica a tabela"


class VinculoIdentidade(models.Model):
    """A ponte entre a identidade central e o usuário local, mais o papel.

    O `identidade_client.resolver_com_vinculo` exige os dois primeiros campos
    (`identidade_id` e `usuario`). O `papel` mora junto porque é a mesma
    pergunta feita duas vezes — "quem é esta pessoa aqui dentro" — e separar em
    outra tabela só criaria um join e um lugar a mais para esquecer de olhar.
    É o mesmo caminho do CRM, que guarda `precisa_trocar_senha` aqui
    (`crm/apps/crm/models/identidade.py:29-32`).
    """

    identidade_id = models.UUIDField(
        "identidade no Conecta ID",
        unique=True,
        db_index=True,
        help_text="Casa por este UUID antes do e-mail: o e-mail muda, o UUID não.",
    )
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vinculo_identidade",
        verbose_name="usuário",
    )
    papel = models.CharField(
        "papel",
        max_length=20,
        choices=Papel.choices,
        blank=True,
        default="",
        help_text="Vazio = entra, mas não autoriza nada. A diretoria promove.",
    )
    precisa_trocar_senha = models.BooleanField(
        "precisa trocar a senha",
        default=False,
        help_text="Cópia local do que o Conecta ID informou no último login. "
        "Quem define senha é o Conecta ID, não este app.",
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "vínculo de identidade"
        verbose_name_plural = "vínculos de identidade"
        ordering = ("usuario__email",)

    def __str__(self) -> str:
        return f"{self.usuario.email or self.usuario.username} ({self.papel or 'sem papel'})"

    # ----- o que cada papel pode -----------------------------------------

    @property
    def pode_autorizar_negociacao(self) -> bool:
        """Destravar valor de produto e cronograma de parcelas numa venda."""
        return self.papel in {Papel.GERENTE, Papel.DIRETORIA}

    @property
    def pode_publicar_tabela(self) -> bool:
        """Alterar a tabela de preços que todo consultor enxerga.

        Mais restrito que autorizar negociação, e a diferença é de alcance: uma
        negociação afeta uma venda, a tabela afeta todas as próximas.
        """
        return self.papel == Papel.DIRETORIA

    def save(self, *args, **kwargs):
        # A diretoria precisa alcançar o /django-admin/, onde ficam as mudanças
        # estruturais do catálogo (criar produto, reordenar, travar categoria).
        # Amarrar `is_staff` ao papel evita a pergunta "por que fulano é
        # diretoria mas não entra no admin" — e evita staff esquecido em quem
        # foi rebaixado.
        staff = self.papel == Papel.DIRETORIA
        if self.usuario_id and self.usuario.is_staff != staff:
            self.usuario.is_staff = staff
            self.usuario.save(update_fields=["is_staff"])
        super().save(*args, **kwargs)


def papel_de(usuario) -> str:
    """O papel de um usuário, ou string vazia. Nunca levanta."""
    vinculo = getattr(usuario, "vinculo_identidade", None)
    return vinculo.papel if vinculo else ""
