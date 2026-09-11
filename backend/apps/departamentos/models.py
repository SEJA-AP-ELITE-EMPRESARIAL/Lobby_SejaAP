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
"""
from django.db import models


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
