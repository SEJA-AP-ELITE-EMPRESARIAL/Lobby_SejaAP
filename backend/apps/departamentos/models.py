"""
Departamentos da Seja AP, como o Omie os tem.

O Omie é a fonte. Esta tabela é um ESPELHO, atualizado de hora em hora pelo
`sincronizar_departamentos` (o container `lobby-departamentos` do compose), e
existe por dois motivos:

1. O navegador do consultor não pode falar com o Omie. A chave do app no Omie é
   do ERP inteiro — lê e escreve o financeiro —, e o front roda na máquina de
   qualquer um.
2. O Omie não pode estar no caminho da venda. Se ele cair ou demorar, o
   consultor continua escolhendo departamento pela última lista boa.

POR QUE NADA É APAGADO

Departamento que sai do Omie (ou é inativado lá) vira `ativo=False`, e a linha
fica. O código dele pode estar no payload de vendas já enviadas, e quem for
conferir uma venda antiga precisa conseguir ler o nome. Se voltar, volta na
mesma linha.

A CONFIGURAÇÃO DA DIRETORIA

Desde 17/09/2026 (TSK-585) a diretoria escolhe, no `/admin`, se o consultor vê a
lista inteira ou um departamento fixo. Isso é `ConfiguracaoDepartamento`, e não
um campo em `Departamento`: a lista é espelho do Omie e o sincronizador reescreve
as linhas dela; a escolha da diretoria não pode depender de ele não mexer.
"""
from django.db import models
from django.db.models import Q


class Departamento(models.Model):
    codigo = models.CharField(
        "código no Omie",
        max_length=20,
        unique=True,
        help_text="O `codigo` do ListarDepartamentos. É o que o n8n repassa ao Omie.",
    )
    descricao = models.CharField("descrição", max_length=120)
    estrutura = models.CharField(
        "estrutura",
        max_length=60,
        blank=True,
        default="",
        help_text="Posição na árvore do Omie (ex.: 001.003). É o que ordena a lista.",
    )
    ativo = models.BooleanField(
        "ativo",
        default=True,
        db_index=True,
        help_text="Falso quando o Omie o marca como inativo ou deixa de listá-lo.",
    )
    visto_em = models.DateTimeField(
        "visto no Omie em",
        help_text="Última sincronização bem-sucedida que o listou, ativo ou não.",
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    alterado_em = models.DateTimeField(
        "alterado em",
        auto_now=True,
        help_text="Última mudança de nome, estrutura ou estado — não a última sincronização.",
    )

    class Meta:
        verbose_name = "departamento"
        verbose_name_plural = "departamentos"
        ordering = ("estrutura", "descricao", "codigo")

    def __str__(self) -> str:
        return self.descricao if self.ativo else f"{self.descricao} (inativo)"


class ConfiguracaoDepartamento(models.Model):
    """Como o campo Departamento aparece para o consultor no Elite e na APN.

    UMA LINHA POR GRAVAÇÃO, E VALE A MAIS RECENTE

    Salvar a aba cria uma linha nova em vez de editar a anterior. É o que responde
    "quem fixou o departamento, e desde quando" sem uma tabela de histórico à
    parte, pela mesma razão do `PublicacaoCatalogo`: tudo que se configura no
    `/admin` fica com autor e data.

    Sem linha nenhuma vale a lista completa, que era o comportamento de antes da
    aba existir. Por isso a migration não semeia nada: o deploy não muda o que o
    consultor vê.

    FIXO INATIVO NÃO TRAVA A VENDA

    Se o departamento fixo for inativado no Omie, o lobby volta a mostrar a lista
    (ver `servicos.fixo_em_vigor`). A linha continua apontando para ele e, se o
    Omie o reativar, o fixo volta a valer sozinho. Mandar um código inativo ao
    Omie falharia a venda no n8n, e travar o campo vazio pararia o consultor.
    """

    class Modo(models.TextChoices):
        LISTA = "lista", "lista completa"
        FIXO = "fixo", "departamento fixo"

    modo = models.CharField("modo", max_length=10, choices=Modo.choices)
    departamento = models.ForeignKey(
        Departamento,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="configuracoes",
        verbose_name="departamento fixo",
        help_text="Só no modo fixo. Departamento nunca é apagado (ver o cabeçalho), "
        "então o PROTECT não deveria disparar nunca.",
    )
    autor = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuracoes_departamento",
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
        verbose_name = "configuração do departamento"
        verbose_name_plural = "configurações do departamento"
        # `-id` desempata duas gravações no mesmo instante: a última é a que vale.
        ordering = ("-criado_em", "-id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(modo="lista", departamento__isnull=True)
                    | Q(modo="fixo", departamento__isnull=False)
                ),
                name="departamento_so_no_modo_fixo",
            ),
        ]

    def __str__(self) -> str:
        if self.modo == self.Modo.FIXO and self.departamento_id:
            return f"fixo: {self.departamento.descricao}"
        return self.get_modo_display()
