"""
Comprovante de venda — a prova de que os valores enviados são legítimos.

O PROBLEMA QUE ISTO RESOLVE

O navegador do consultor monta o payload da venda e o posta DIRETO no webhook
público do n8n (`index.html:471-483`). O webhook não valida nada. Então, até
aqui, todo o controle de valores era cosmético: bastava abrir o DevTools, mudar
`valor_total` e postar. O cadeado na tela impedia o clique, não a venda.

Nenhuma mudança no front resolve isso — o código roda na máquina de quem se
quer impedir. A única correção possível envolve o servidor atestar os valores e
o n8n exigir esse atestado.

COMO FUNCIONA

1. Antes de enviar, o front pede um comprovante ao Lobby, passando os valores.
2. O servidor só emite se eles forem legítimos: ou batem com a tabela de preços
   do banco, ou vêm acompanhados da credencial de quem autorizou a negociação.
3. O comprovante é `<uuid>.<hmac>` e vai junto no payload do n8n.
4. O n8n, antes de processar, chama `/api/venda/validar`. O Lobby confere a
   assinatura, confere que os valores não mudaram no caminho, consome o nonce e
   responde.

Isso fecha três coisas de uma vez: forjar (precisa da chave), reusar (o nonce é
de uso único) e adulterar (a assinatura cobre os valores).

O QUE ISTO NÃO FECHA

O webhook do n8n continua público. Ninguém consegue mais criar uma venda com
valor inventado, mas ainda dá para enchê-lo de lixo. Limitar isso é do lado do
n8n, não daqui.
"""
import hashlib
import json
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

# Curta de propósito: o comprovante é emitido segundos antes do envio. Uma
# validade longa vira uma janela para reusar um comprovante legítimo num payload
# adulterado — e o nonce só protege contra a SEGUNDA tentativa.
VALIDADE_MINUTOS = 15

SALT = "lobby.comprovante.venda"


def canoniza(valores: dict) -> str:
    """Forma canônica dos valores assinados.

    Chaves ordenadas e sem espaços: o mesmo conteúdo tem que produzir sempre a
    mesma string, senão a validação falha por um detalhe de serialização em vez
    de por adulteração — e ninguém descobre por quê.
    """
    return json.dumps(valores, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_dos_valores(valores: dict) -> str:
    return hashlib.sha256(canoniza(valores).encode("utf-8")).hexdigest()


class ComprovanteVenda(models.Model):
    """Um atestado de uso único, emitido pelo servidor, para uma venda."""

    class Fluxo(models.TextChoices):
        ELITE = "elite", "Elite (produto de tabela)"
        APN = "apn", "APN (valor livre)"
        # O DH é o primeiro fluxo de PRODUTO, não de categoria: quem manda o
        # consultor para o Formulário DH é o `Produto.fluxo` do Recrutamento e
        # Seleção. O comprovante não muda de natureza por isso — continua sendo
        # "estes valores saíram do Lobby" — mas o que ele atesta é a soma dos
        # adiantamentos do quadro de vagas, e não um preço de tabela.
        DH = "dh", "DH (valores do formulário)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    protocolo = models.CharField(
        "protocolo",
        max_length=40,
        db_index=True,
        help_text="O código da venda gerado no cliente (SSS-YYMMDDPRRRRR).",
    )
    fluxo = models.CharField("fluxo", max_length=10, choices=Fluxo.choices)
    negociado = models.BooleanField(
        "valores negociados",
        default=False,
        help_text="Falso = preço de tabela. Verdadeiro exigiu autorização.",
    )

    valores = models.JSONField("valores atestados")
    hash_valores = models.CharField("hash dos valores", max_length=64, db_index=True)

    autorizador = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comprovantes_autorizados",
        verbose_name="quem autorizou",
    )
    autorizador_email = models.EmailField(
        "e-mail de quem autorizou",
        blank=True,
        default="",
        help_text="Cópia no momento da emissão — a conta pode ser desativada "
        "depois, e o histórico não pode perder o nome de quem assinou.",
    )

    emitido_em = models.DateTimeField("emitido em", auto_now_add=True)
    expira_em = models.DateTimeField("expira em", db_index=True)
    consumido_em = models.DateTimeField("consumido em", null=True, blank=True)
    ip_emissao = models.GenericIPAddressField("IP na emissão", null=True, blank=True)

    class Meta:
        verbose_name = "comprovante de venda"
        verbose_name_plural = "comprovantes de venda"
        ordering = ("-emitido_em",)
        indexes = [models.Index(fields=["consumido_em", "expira_em"])]

    def __str__(self) -> str:
        quem = self.autorizador_email or "tabela"
        return f"{self.protocolo} ({quem})"

    # ----- assinatura ----------------------------------------------------

    def _assinatura(self) -> str:
        """HMAC sobre o id E o hash dos valores.

        Incluir o hash é o que impede trocar o corpo mantendo o mesmo id: o
        comprovante não atesta "esta venda existe", atesta "estes valores foram
        aprovados".
        """
        return salted_hmac(
            SALT, f"{self.id}:{self.hash_valores}", secret=settings.SECRET_KEY
        ).hexdigest()

    @property
    def token(self) -> str:
        return f"{self.id}.{self._assinatura()}"

    def assinatura_confere(self, assinatura: str) -> bool:
        return constant_time_compare(self._assinatura(), assinatura or "")

    # ----- estado --------------------------------------------------------

    @property
    def expirado(self) -> bool:
        return timezone.now() >= self.expira_em

    @property
    def consumido(self) -> bool:
        return self.consumido_em is not None
