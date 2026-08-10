"""
O catálogo de produtos do Lobby.

Substituiu o array JSON que morava no Cloudflare KV e, antes disso, o literal
`CATS` do próprio `index.html`. A migração para cá é o que permite responder
"quem alterou este preço, e quando" — pergunta que um blob JSON com um único
carimbo `atualizadoEm` nunca conseguiu responder.

SOBRE AS CITAÇÕES A `functions/...` NESTE APP

Vários comentários daqui citam arquivos de `functions/`, a implementação em
Cloudflare Pages Functions que este backend substituiu. Ela **não existe mais na
árvore** — foi removida junto com esta nota. As citações continuam porque
explicam de onde cada regra veio; para lê-las, use o git:

    git show 338e932 -- functions/_lib/catalogo.js

Duas decisões de modelagem que valem explicação, porque não são óbvias:

1. `preco` de produto recorrente NÃO é coluna. É `mensalidade × vigencia_meses`,
   calculado na leitura. No KV isso era uma regra do validador (`catalogo.js:138`),
   ou seja, uma promessa que dependia de todo mundo passar por ele. Aqui vira
   impossibilidade: não existe lugar onde guardar um total divergente.
   Isso importa mais do que parece — o front trava a venda se os dois
   discordarem (o total exibido vem de `monthly × vigencia`, mas o cronograma é
   ancorado em `price`; se `price < monthly×vigencia`, o consultor não consegue
   avançar da etapa de pagamento).

2. Categoria de fluxo próprio (a APN) é LINHA, não constante de código.
   No KV ela era um objeto em `catalogo.js:60-69`, removido na escrita e
   reinserido na leitura. O discriminador aqui é `fluxo`, não o slug — porque é
   `flow` que o front realmente lê (`index.html:597`, `:828`, `:1297`); ele
   nunca compara o id com 'apn'. Manter a regra presa ao slug seria copiar um
   detalhe de implementação em vez do contrato.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

# Tetos herdados do validador do KV (`functions/_lib/catalogo.js:128-141`).
# Não são regra de negócio — são rede de segurança contra dedo escorregando no
# teclado. Um zero a mais na mensalidade não pode virar contrato.
VIGENCIA_MIN = 1
VIGENCIA_MAX = 120
MENSALIDADE_MAX = Decimal("10000000")
VALOR_MAX = Decimal("100000000")

CENTAVO = Decimal("0.01")


def arredonda(valor) -> Decimal:
    """Equivalente ao `round2` do KV (`catalogo.js:88`), em aritmética decimal."""
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


class Cor(models.TextChoices):
    """As quatro cores que o front sabe pintar (`index.html:586-591`).

    Fora dessas, `COLORS[cat.color]` vira `undefined` e a tela quebra ao abrir a
    categoria. O KV se protegia caindo em 'gold' silenciosamente
    (`catalogo.js:158`); aqui o banco recusa antes.
    """

    GOLD = "gold", "Dourado"
    BLUE = "blue", "Azul"
    GREEN = "green", "Verde"
    PURPLE = "purple", "Roxo"


class Categoria(models.Model):
    """Um card do lobby. A ordem das linhas é a ordem dos cards na tela."""

    slug = models.SlugField(
        "identificador",
        max_length=40,
        unique=True,
        help_text="Vai para o JSON como `id`. Não mude depois de publicado: "
        "ele aparece no payload da venda enviado ao n8n.",
    )
    nome = models.CharField("nome", max_length=80)
    descricao = models.CharField("descrição", max_length=240, blank=True, default="")
    icone = models.CharField(
        "ícone",
        max_length=60,
        default="diamond",
        help_text="Nome de um Material Symbol.",
    )
    cor = models.CharField("cor", max_length=10, choices=Cor.choices, default=Cor.GOLD)
    travada = models.BooleanField(
        "em implementação",
        default=False,
        help_text="Aparece no lobby, mas não abre. Vai ao JSON como `locked`.",
    )
    fluxo = models.CharField(
        "fluxo próprio",
        max_length=20,
        blank=True,
        default="",
        help_text="Vazio = fluxo padrão (escolher produto da tabela). "
        "'apn' = o consultor informa o valor da venda, sem tabela de preços. "
        "Categoria com fluxo próprio é somente leitura: não se edita pelo /admin.",
    )
    sigla = models.CharField(
        "sigla",
        max_length=3,
        blank=True,
        default="",
        validators=[RegexValidator(r"^[A-Z]{3}$", "Use exatamente 3 letras maiúsculas.")],
        help_text="Só usada em categoria de fluxo próprio, onde não há produto "
        "para tirar a sigla do protocolo da venda.",
    )
    ordem = models.PositiveIntegerField("ordem", default=0)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ("ordem", "id")

    def __str__(self) -> str:
        return self.nome

    @property
    def gerenciada_em_codigo(self) -> bool:
        """Categoria de fluxo próprio não tem tabela de preços para editar.

        É o que substitui o `semCategoriasDeCodigo` do KV (`catalogo.js:75-77`) —
        com a diferença de que agora ela EXISTE no banco e aparece no admin, em
        vez de ser um objeto invisível reinserido na resposta.
        """
        return bool(self.fluxo)


class Produto(models.Model):
    """Um item da tabela de preços. A APN não tem nenhum — e é o único caso."""

    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.PROTECT,
        related_name="produtos",
        verbose_name="categoria",
        help_text="PROTECT de propósito: produto já vendido não pode sumir do "
        "catálogo e deixar protocolos órfãos.",
    )
    slug = models.SlugField("identificador", max_length=40)
    nome = models.CharField("nome", max_length=120)
    sigla = models.CharField(
        "sigla",
        max_length=3,
        validators=[RegexValidator(r"^[A-Z]{3}$", "Use exatamente 3 letras maiúsculas.")],
        help_text="Alimenta o protocolo da venda (SSS-YYMMDDPRRRRR).",
    )
    descricao = models.CharField("descrição", max_length=240, blank=True, default="")
    duracao = models.CharField(
        "duração",
        max_length=40,
        blank=True,
        default="",
        help_text="Texto de exibição ('12 meses', '3 dias'). Nunca é interpretado — "
        "quem manda no cálculo é `vigencia_meses`.",
    )
    icone = models.CharField("ícone", max_length=60, default="workspace_premium")

    recorrente = models.BooleanField(
        "recorrente",
        default=False,
        help_text="Recorrente: cobra mensalidade e o total é derivado. "
        "Avulso: valor único à vista.",
    )
    mensalidade = models.DecimalField(
        "mensalidade",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(CENTAVO), MaxValueValidator(MENSALIDADE_MAX)],
        help_text="Só em produto recorrente.",
    )
    vigencia_meses = models.PositiveSmallIntegerField(
        "vigência (meses)",
        null=True,
        blank=True,
        validators=[MinValueValidator(VIGENCIA_MIN), MaxValueValidator(VIGENCIA_MAX)],
        help_text="Só em produto recorrente.",
    )
    valor = models.DecimalField(
        "valor à vista",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(CENTAVO), MaxValueValidator(VALOR_MAX)],
        help_text="Só em produto avulso. Em recorrente o total é calculado, "
        "nunca digitado — por isso esta coluna fica nula lá.",
    )
    ordem = models.PositiveIntegerField("ordem", default=0)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "produto"
        verbose_name_plural = "produtos"
        ordering = ("categoria", "ordem", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("categoria", "slug"), name="produto_slug_unico_na_categoria"
            ),
            # As duas metades da mesma invariante. Escritas como constraint, e não
            # como validação de formulário, porque o /django-admin/ e um shell
            # `manage.py` passam por fora de qualquer serializer.
            #
            # `condition=` desde o Django 6.0. O kwarg antigo (`check=`) foi
            # depreciado na 5.1 e REMOVIDO na 6.0 — nele, o import do app inteiro
            # morre com TypeError. A migration 0001 também foi atualizada: ela não
            # reexecuta, mas é importada toda vez que o Django carrega o histórico.
            models.CheckConstraint(
                name="produto_recorrente_tem_mensalidade_e_vigencia",
                condition=models.Q(recorrente=False)
                | models.Q(
                    recorrente=True,
                    mensalidade__isnull=False,
                    vigencia_meses__isnull=False,
                    valor__isnull=True,
                ),
            ),
            models.CheckConstraint(
                name="produto_avulso_tem_valor",
                condition=models.Q(recorrente=True)
                | models.Q(
                    recorrente=False,
                    valor__isnull=False,
                    mensalidade__isnull=True,
                    vigencia_meses__isnull=True,
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.categoria.slug}/{self.slug}"

    @property
    def preco(self) -> Decimal:
        """O total do contrato — `price` no JSON.

        Derivado em recorrente, guardado em avulso. Esta é a única fonte do
        campo: não existe coluna `preco`, então não existe estado inconsistente.
        """
        if self.recorrente:
            return arredonda(self.mensalidade * self.vigencia_meses)
        return arredonda(self.valor)


class PublicacaoCatalogo(models.Model):
    """Histórico de publicações da tabela de preços.

    O KV gravava uma cópia em `historico:<ISO>` com TTL de 90 dias e nenhum
    endpoint para ler (`functions/api/catalogo.js:35-37`) — na prática, um
    backup que ninguém abria e que expirava sozinho. Aqui a cópia vira registro
    consultável e, o que o KV não tinha, carrega o AUTOR.

    O expurgo passa a ser explícito (comando de manage), não um TTL invisível.
    """

    publicado_em = models.DateTimeField("publicado em", auto_now_add=True)
    autor = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="publicacoes_catalogo",
        verbose_name="autor",
        help_text="Nulo só para a semente inicial, que não tem autor humano.",
    )
    autor_email = models.EmailField(
        "e-mail do autor",
        blank=True,
        default="",
        help_text="Cópia do e-mail no momento da publicação. Guardado à parte "
        "porque a conta pode ser desativada depois, e o histórico não pode "
        "perder o nome de quem assinou a mudança.",
    )
    catalogo = models.JSONField(
        "catálogo publicado",
        help_text="O JSON exatamente como foi servido depois desta publicação.",
    )
    resumo = models.TextField(
        "resumo das alterações",
        blank=True,
        default="",
        help_text="O que mudou em relação à publicação anterior, em texto.",
    )

    class Meta:
        verbose_name = "publicação do catálogo"
        verbose_name_plural = "publicações do catálogo"
        ordering = ("-publicado_em",)

    def __str__(self) -> str:
        quem = self.autor_email or "semente"
        return f"{self.publicado_em:%d/%m/%Y %H:%M} — {quem}"
