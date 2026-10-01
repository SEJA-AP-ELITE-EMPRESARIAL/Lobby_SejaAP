"""
Categorias financeiras da venda da APN (TSK-877, 01/10/2026).

O NOME

No Lobby, "categoria" já é outra coisa: o card do catálogo (`apps.catalogo`),
e o payload da APN já leva `categoria: {id: "apn"}`. Esta é a categoria em que a
venda entra no financeiro, e por isso o código a chama de categoria FINANCEIRA,
mesmo que a tela diga só "Categoria".

O DESENHO É O DO DEPARTAMENTO

Mesma estrutura de `apps.departamentos`, e pelos mesmos motivos (ver o cabeçalho
de `apps/departamentos/models.py`):

- `CategoriaFinanceira` é um ESPELHO da fonte, atualizado de hora em hora pelo
  `sincronizar_categorias_financeiras` (o container `lobby-categorias` do
  compose). O navegador nunca fala com a fonte, e a fonte fora do ar não trava
  venda.
- Nada é apagado: quem sai da fonte vira `ativo=False`, porque o código pode estar
  no payload de vendas já enviadas.
- `ConfiguracaoCategoriaFinanceira` é a escolha da diretoria no `/admin` (lista
  completa ou categoria fixa), uma linha por gravação.

A diferença é o alcance: o departamento vale para o Elite e para a APN; a
categoria, só para a APN.
"""
from django.db import models
from django.db.models import Q


class CategoriaFinanceira(models.Model):
    codigo = models.CharField(
        "código na fonte",
        max_length=40,
        unique=True,
        help_text="O código que a fonte dá à categoria. É o que vai no payload da venda.",
    )
    descricao = models.CharField("descrição", max_length=120)
    ativo = models.BooleanField(
        "ativo",
        default=True,
        db_index=True,
        help_text="Falso quando a fonte a marca como inativa ou deixa de listá-la.",
    )
    visto_em = models.DateTimeField(
        "visto na fonte em",
        help_text="Última sincronização bem-sucedida que a listou, ativa ou não.",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    alterado_em = models.DateTimeField(
        "alterado em",
        auto_now=True,
        help_text="Última mudança de nome ou de estado — não a última sincronização.",
    )

    class Meta:
        verbose_name = "categoria financeira"
        verbose_name_plural = "categorias financeiras"
        ordering = ("descricao", "codigo")

    def __str__(self) -> str:
        return self.descricao if self.ativo else f"{self.descricao} (inativa)"


class ConfiguracaoCategoriaFinanceira(models.Model):
    """Como o campo Categoria aparece para o consultor na APN.

    Mesmas regras de `ConfiguracaoDepartamento`: uma linha por gravação e vale a
    mais recente; sem linha nenhuma vale a lista completa; e uma categoria fixa
    que fique inativa na fonte devolve a lista ao consultor, em vez de travar a
    venda (ver `servicos.fixa_em_vigor`).
    """

    class Modo(models.TextChoices):
        LISTA = "lista", "lista completa"
        FIXO = "fixo", "categoria fixa"

    modo = models.CharField("modo", max_length=10, choices=Modo.choices)
    categoria = models.ForeignKey(
        CategoriaFinanceira,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="configuracoes",
        verbose_name="categoria fixa",
        help_text="Só no modo fixo. Categoria nunca é apagada (ver o cabeçalho), "
        "então o PROTECT não deveria disparar nunca.",
    )
    autor = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuracoes_categoria_financeira",
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
        verbose_name = "configuração da categoria financeira"
        verbose_name_plural = "configurações da categoria financeira"
        # `-id` desempata duas gravações no mesmo instante: a última é a que vale.
        ordering = ("-criado_em", "-id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(modo="lista", categoria__isnull=True)
                    | Q(modo="fixo", categoria__isnull=False)
                ),
                name="categoria_financeira_so_no_modo_fixo",
            ),
        ]

    def __str__(self) -> str:
        if self.modo == self.Modo.FIXO and self.categoria_id:
            return f"fixa: {self.categoria.descricao}"
        return self.get_modo_display()
