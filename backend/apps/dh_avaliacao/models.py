"""
Os valores das modalidades do DH - Avaliação (TSK-1056, 14/10/2026).

O DH - Avaliação (produto `avaliacoes`, fluxo `dh_avaliacao`) vende o PROFILER
em itens: tantos Relatórios a tanto, tantas Devolutivas a tanto. O valor
unitário de cada modalidade é do consultor, que o digita venda a venda. O que a
diretoria decide, na aba DH - Avaliação do `/admin`, é:

- com que valor cada modalidade ABRE na tela do consultor;
- se esse valor é FIXO. Fixo, o campo vem travado no lobby, e o comprovante da
  venda recusa qualquer outro valor (`apps.vendas.servicos`).

É o desenho do departamento fixo (`apps.departamentos`), e pelas mesmas razões:

- uma linha por gravação, e vale a mais recente. Quem fixou o valor, e desde
  quando, é a própria tabela que responde;
- quem aplica a trava é o backend, pela rota pública que o lobby lê. Um lobby
  aberto desde antes da gravação pega a trava na reconsulta seguinte, e um
  payload montado à mão para fugir dela não ganha comprovante. O comprovante só
  segura de verdade quando o n8n fatura pelos itens assinados
  (`comprovante_valores.itens`) — ver `deploy/N8N-VALIDACAO.md`.

Sem linha nenhuma valem os VALORES INICIAIS do pedido — Relatório R$ 150 e
Devolutiva R$ 450, livres —, e a migration não semeia nada: uma gravação sem
pessoa por trás seria um autor inventado.

Por que colunas, e não um JSON por modalidade: são duas modalidades, ditas pelo
dono, e cada uma é um campo do formulário do `index.html`. Modalidade nova é
deploy do front de qualquer jeito; com coluna, o banco segura valor zero ou
negativo, coisa que um JSON não seguraria.
"""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

CENTAVO = Decimal("0.01")
# Rede de segurança contra dedo escorregando, como os tetos do catálogo. Um zero
# a mais no valor de um relatório não pode virar o valor de toda venda.
VALOR_UNITARIO_MAX = Decimal("1000000")
# O mesmo para a quantidade de uma modalidade numa venda.
QUANTIDADE_MAX = 10000


class Modalidade(models.TextChoices):
    """As modalidades vendidas. O valor é a chave `modalidade` do payload do n8n."""

    RELATORIO = "relatorio", "Relatório"
    DEVOLUTIVA = "devolutiva", "Devolutiva"


# "do Relatório", "da Devolutiva": as mensagens da tela falam das duas.
DA_MODALIDADE = {
    Modalidade.RELATORIO: "do Relatório",
    Modalidade.DEVOLUTIVA: "da Devolutiva",
}

# Os valores com que cada modalidade abre enquanto a diretoria não salvar a aba.
VALORES_INICIAIS = {
    Modalidade.RELATORIO: Decimal("150.00"),
    Modalidade.DEVOLUTIVA: Decimal("450.00"),
}


def _valor(modalidade):
    return models.DecimalField(
        f"valor {DA_MODALIDADE[modalidade]}",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(CENTAVO), MaxValueValidator(VALOR_UNITARIO_MAX)],
        help_text="Valor unitário com que a modalidade abre no lobby.",
    )


def _fixo(modalidade):
    return models.BooleanField(
        f"valor {DA_MODALIDADE[modalidade]} fixo",
        default=False,
        help_text="Fixo: o lobby trava o campo neste valor, e o comprovante da venda "
        "recusa outro. Livre: é só o valor com que o campo abre.",
    )


class ConfiguracaoValores(models.Model):
    """Uma gravação da aba DH - Avaliação. Vale a mais recente."""

    relatorio_valor = _valor(Modalidade.RELATORIO)
    relatorio_fixo = _fixo(Modalidade.RELATORIO)
    devolutiva_valor = _valor(Modalidade.DEVOLUTIVA)
    devolutiva_fixo = _fixo(Modalidade.DEVOLUTIVA)

    autor = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuracoes_dh_avaliacao",
        verbose_name="autor",
    )
    autor_email = models.EmailField(
        "e-mail do autor",
        blank=True,
        default="",
        help_text="Cópia do e-mail no momento da gravação: a conta pode ser "
        "desativada depois, e o registro não pode perder quem decidiu.",
    )
    criado_em = models.DateTimeField("gravado em", auto_now_add=True)

    class Meta:
        verbose_name = "configuração dos valores do DH - Avaliação"
        verbose_name_plural = "configurações dos valores do DH - Avaliação"
        # `-id` desempata duas gravações no mesmo instante: a última é a que vale.
        ordering = ("-criado_em", "-id")
        constraints = [
            # A validação mora no serviço; o CHECK segura quem gravar por fora
            # dele. Valor zero fixo travaria toda venda num item de graça.
            models.CheckConstraint(
                condition=Q(relatorio_valor__gt=0) & Q(devolutiva_valor__gt=0),
                name="dh_avaliacao_valores_positivos",
            ),
        ]

    def __str__(self) -> str:
        partes = []
        for modalidade in Modalidade:
            valor = getattr(self, f"{modalidade.value}_valor")
            fixo = getattr(self, f"{modalidade.value}_fixo")
            partes.append(f"{modalidade.label} R$ {valor} ({'fixo' if fixo else 'livre'})")
        return " · ".join(partes)

    def da_modalidade(self, modalidade) -> tuple[Decimal, bool]:
        """(valor, fixo) de uma modalidade nesta gravação."""
        chave = Modalidade(modalidade).value
        return getattr(self, f"{chave}_valor"), getattr(self, f"{chave}_fixo")
