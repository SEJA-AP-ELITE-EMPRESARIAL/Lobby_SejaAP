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

3. `sigla` é ÚNICA no catálogo inteiro, produtos e categorias juntos.
   Ela é o `SSS` do protocolo da venda (`SSS-YYMMDDPRRRRR`), e o protocolo é o
   que o n8n, o Omie e a planilha do comercial usam para saber O QUE foi
   vendido. Duas linhas com a mesma sigla não dão erro em lugar nenhum: geram
   protocolos indistinguíveis, e a ambiguidade só aparece meses depois, na
   conciliação. Como o catálogo é editado à mão no /django-admin/ e crescer é o
   caminho normal (a Elite vai receber produtos novos), a garantia tem que estar
   no banco, não na atenção de quem digita.

4. FLUXO PRÓPRIO existe em DOIS níveis, e eles não são o mesmo.
   `Categoria.fluxo` é a APN: a categoria inteira não tem tabela e não tem
   produto a escolher. `Produto.fluxo` (17/08/2026 → 25/08/2026) é o
   Recrutamento e Seleção: a categoria "Produtos" lista produtos normalmente, e
   é o produto escolhido que manda o consultor para um formulário próprio em vez
   do cronograma de parcelas. Foi preciso separar porque "Produtos" nasceu para
   receber itens sem relação entre si — o próximo pode ser um produto de tabela,
   e um fluxo na categoria trancaria todos no mesmo formulário.

5. A REGRA DE DATA do cronograma é dado, não código.
   Até 17/08/2026 o dia do vencimento (15) e o mês da primeira parcela viviam em
   constantes do `index.html`: mudar o dia de cobrança era deploy do front. Como
   essa data muda com frequência — e sempre com pressa, porque acompanha
   calendário de cobrança — ela virou `PoliticaCobranca`, editável na tela da
   diretoria. O front lê a política do mesmo `GET /api/catalogo` de onde já lê
   preço; as constantes que sobraram lá são só o fallback offline.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
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

# O dia do vencimento para em 28 de propósito. O ponto de fixar um dia é ter UMA
# data de cobrança em lote; 29, 30 e 31 não existem em todo mês e reintroduziriam
# a regra de "cair no último dia" que a mudança de 17/08/2026 justamente eliminou.
DIA_VENCIMENTO_MIN = 1
DIA_VENCIMENTO_MAX = 28
# Teto do adiamento da entrada. Entrada é o ato de fechar a venda: prazo longo
# deixa de ser entrada e vira parcela, e o cronograma não saberia disso.
ENTRADA_PRAZO_MAX = 90


def arredonda(valor) -> Decimal:
    """Equivalente ao `round2` do KV (`catalogo.js:88`), em aritmética decimal."""
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def normaliza_sigla(valor) -> str:
    """Sigla sempre em maiúsculas e sem espaço em volta.

    Digitar `pre` no /django-admin/ é distração de digitação, não erro de
    conteúdo — e sem isto o validador devolveria "use exatamente 3 letras
    maiúsculas" para quem escreveu a sigla certa em minúscula.
    """
    return str(valor or "").strip().upper()


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


class FluxoProduto(models.TextChoices):
    """Formulário próprio de um PRODUTO — o que entra no lugar da tabela de preços.

    Não confundir com `Categoria.fluxo`. Lá o fluxo é da categoria inteira (a
    APN: não há produto a escolher). Aqui ele é de UM produto dentro de uma
    categoria comum: a categoria continua listando produtos, e é o produto
    escolhido que decide para qual formulário o consultor vai.

    A separação existe porque "Produtos" nasceu para receber vários itens sem
    relação entre si — o Recrutamento e Seleção é o primeiro, e o próximo pode
    perfeitamente ser um produto de tabela. Um fluxo na categoria trancaria
    todos eles no mesmo formulário.

    Ao contrário de `Categoria.fluxo`, este campo tem CHOICES: cada valor daqui
    corresponde a um formulário escrito no `index.html`, e um valor que o front
    não conhece é um produto que abre sem preço e sem formulário — quebrado, em
    silêncio. Acrescentar um valor aqui é sempre deploy do front junto.
    """

    DH = "dh", "Formulário DH (Recrutamento e Seleção)"


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
        "para tirar a sigla do protocolo da venda. Não pode repetir a sigla de "
        "nenhum produto nem de outra categoria.",
    )
    valor_referencia = models.DecimalField(
        "valor de referência",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(CENTAVO), MaxValueValidator(VALOR_MAX)],
        help_text="Só em categoria de fluxo próprio (a APN). É o valor com que a "
        "tela do consultor ABRE — ele continua livre para alterar, porque a APN é "
        "venda de valor negociado. Vazio = a tela abre em branco, como antes.",
    )
    ordem = models.PositiveIntegerField("ordem", default=0)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ("ordem", "id")
        constraints = [
            # Valor de referência só faz sentido onde não há produto de tabela.
            # Numa categoria comum ele seria um número que ninguém lê — e o
            # primeiro a lê-lo por engano estaria cotando errado.
            models.CheckConstraint(
                name="valor_referencia_so_em_fluxo_proprio",
                condition=models.Q(valor_referencia__isnull=True)
                | ~models.Q(fluxo=""),
            ),
        ]

    def __str__(self) -> str:
        return self.nome

    def clean_fields(self, exclude=None):
        self.sigla = normaliza_sigla(self.sigla)
        super().clean_fields(exclude=exclude)

    def clean(self):
        conflito = sigla_em_conflito(self.sigla, ignorando_categoria=self.pk)
        if conflito:
            raise ValidationError({"sigla": f"A sigla {self.sigla} já é de {conflito}."})

    def save(self, *args, **kwargs):
        # Também no save: shell, migration e script não passam por full_clean().
        self.sigla = normaliza_sigla(self.sigla)
        super().save(*args, **kwargs)

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
        unique=True,
        validators=[RegexValidator(r"^[A-Z]{3}$", "Use exatamente 3 letras maiúsculas.")],
        help_text="Alimenta o protocolo da venda (SSS-YYMMDDPRRRRR). Única no "
        "catálogo inteiro: é por ela que se sabe, meses depois, o que foi vendido.",
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

    fluxo = models.CharField(
        "fluxo próprio",
        max_length=20,
        blank=True,
        default="",
        choices=FluxoProduto.choices,
        help_text="Vazio = produto de tabela: tem preço aqui e o consultor segue "
        "o fluxo padrão. Preenchido, o produto NÃO tem preço — o consultor cai "
        "num formulário próprio e os valores saem de lá. Produto assim é "
        "somente leitura para a tela da diretoria.",
    )

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
            # As três metades — sim, três — da mesma invariante: um produto é
            # recorrente, ou avulso, ou de fluxo próprio, e cada um desses tem
            # exatamente um conjunto de colunas preenchido. O `~Q(fluxo="")` nas
            # duas primeiras é o que abre espaço para a terceira: produto de
            # formulário não tem preço nenhum para conferir.
            #
            # Escritas como constraint, e não como validação de formulário,
            # porque o /django-admin/ e um shell `manage.py` passam por fora de
            # qualquer serializer.
            #
            # `condition=` desde o Django 6.0. O kwarg antigo (`check=`) foi
            # depreciado na 5.1 e REMOVIDO na 6.0 — nele, o import do app inteiro
            # morre com TypeError. A migration 0001 também foi atualizada: ela não
            # reexecuta, mas é importada toda vez que o Django carrega o histórico.
            models.CheckConstraint(
                name="produto_recorrente_tem_mensalidade_e_vigencia",
                condition=~models.Q(fluxo="")
                | models.Q(recorrente=False)
                | models.Q(
                    recorrente=True,
                    mensalidade__isnull=False,
                    vigencia_meses__isnull=False,
                    valor__isnull=True,
                ),
            ),
            models.CheckConstraint(
                name="produto_avulso_tem_valor",
                condition=~models.Q(fluxo="")
                | models.Q(recorrente=True)
                | models.Q(
                    recorrente=False,
                    valor__isnull=False,
                    mensalidade__isnull=True,
                    vigencia_meses__isnull=True,
                ),
            ),
            # Produto de fluxo próprio não guarda valor NENHUM. Um preço parado
            # nessas colunas seria lido por quem não sabe da história — no
            # `/admin`, num relatório — e cotado como se fosse tabela.
            models.CheckConstraint(
                name="produto_de_fluxo_proprio_nao_tem_preco",
                condition=models.Q(fluxo="")
                | models.Q(
                    recorrente=False,
                    mensalidade__isnull=True,
                    vigencia_meses__isnull=True,
                    valor__isnull=True,
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.categoria.slug}/{self.slug}"

    def clean_fields(self, exclude=None):
        self.sigla = normaliza_sigla(self.sigla)
        super().clean_fields(exclude=exclude)

    def clean(self):
        # Produto contra produto já é o `unique=True` do campo (e um índice no
        # banco). Aqui falta o outro lado: a sigla de uma categoria de fluxo
        # próprio, que ocupa o mesmo espaço de nomes no protocolo.
        conflito = sigla_em_conflito(self.sigla, ignorando_produto=self.pk)
        if conflito:
            raise ValidationError({"sigla": f"A sigla {self.sigla} já é de {conflito}."})

        # A mesma regra da `CheckConstraint`, dita antes e em português: o banco
        # recusa com um erro que não ajuda quem está no formulário do
        # /django-admin/.
        if self.fluxo and (
            self.recorrente
            or self.mensalidade is not None
            or self.vigencia_meses is not None
            or self.valor is not None
        ):
            raise ValidationError(
                {
                    "fluxo": "Produto de fluxo próprio não tem preço: os valores "
                    "saem do formulário. Limpe mensalidade, vigência e valor à "
                    "vista, e desmarque recorrente."
                }
            )

    def save(self, *args, **kwargs):
        # Também no save: shell, migration e script não passam por full_clean().
        self.sigla = normaliza_sigla(self.sigla)
        self.fluxo = str(self.fluxo or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def de_formulario(self) -> bool:
        """Produto que abre formulário próprio, sem passar pela tabela de preços.

        O equivalente, no produto, do `gerenciada_em_codigo` da categoria — e o
        critério é o mesmo: o `fluxo`, nunca o slug.
        """
        return bool(self.fluxo)

    @property
    def preco(self) -> Decimal | None:
        """O total do contrato — `price` no JSON.

        Derivado em recorrente, guardado em avulso. Esta é a única fonte do
        campo: não existe coluna `preco`, então não existe estado inconsistente.

        `None` em produto de fluxo próprio, e é o valor certo: não é zero (zero
        é um preço, e um preço errado), é a ausência de preço. Quem serializa
        omite a chave; quem exibe mostra "definido no formulário".
        """
        if self.fluxo:
            return None
        if self.recorrente:
            return arredonda(self.mensalidade * self.vigencia_meses)
        return arredonda(self.valor)


def sigla_em_conflito(sigla, *, ignorando_produto=None, ignorando_categoria=None):
    """Quem já usa esta sigla no catálogo — ou `None` se ela está livre.

    Produtos e categorias dividem UM espaço de nomes, porque o protocolo da venda
    tem um só campo `SSS`: ele vem da sigla do produto no fluxo padrão e da sigla
    da categoria no fluxo próprio (`index.html`, `buildProtocol`). Uma sigla que
    aparece nos dois lados produz protocolos que ninguém consegue desempatar.

    Devolve texto pronto para a mensagem de erro ('o produto "ELITE PRO"'), e não
    o objeto: quem chama só quer dizer à pessoa o que está no caminho.
    """
    sigla = normaliza_sigla(sigla)
    if not sigla:
        return None

    produtos = Produto.objects.filter(sigla=sigla)
    if ignorando_produto:
        produtos = produtos.exclude(pk=ignorando_produto)
    produto = produtos.first()
    if produto:
        return f'o produto "{produto.nome}"'

    categorias = Categoria.objects.exclude(sigla="").filter(sigla=sigla)
    if ignorando_categoria:
        categorias = categorias.exclude(pk=ignorando_categoria)
    categoria = categorias.first()
    if categoria:
        return f'a categoria "{categoria.nome}"'

    return None


class PrimeiroVencimento(models.TextChoices):
    """Em que mês cai a PRIMEIRA parcela.

    As duas leituras possíveis de "vencimento no dia N", e a diferença aparece só
    quando a venda acontece antes do dia N:

    - MES_SEGUINTE: venda 03/09 → 15/10. Um pagamento por mês, sempre: a entrada
      cobre o mês da venda e cada parcela cobre um mês seguinte.
    - PROXIMO: venda 03/09 → 15/09. A primeira parcela vem antes, mas o mês da
      venda fica com duas cobranças (entrada + parcela) e um contrato de 12 meses
      se paga em 11.
    """

    MES_SEGUINTE = "mes_seguinte", "Dia do vencimento do mês SEGUINTE ao da venda"
    PROXIMO = "proximo", "Primeiro dia do vencimento DEPOIS da venda"


class PoliticaCobranca(models.Model):
    """Como o cronograma de uma venda nasce — o que era constante no `index.html`.

    Uma linha vale para o Lobby inteiro (`geral=True`); cada produto pode ter a
    sua, e o que não for preenchido lá herda a geral. Foi a forma escolhida com o
    usuário: o caso comum é uma regra só, e a exceção não pode exigir deploy.

    Não confundir com NEGOCIAÇÃO. Isto é o padrão com que o cronograma abre, e
    vale para todas as vendas seguintes — por isso é da diretoria, mesmo alcance
    da tabela de preços. Mudar a data de UMA venda continua sendo autorização de
    gerente, na própria tela do consultor.
    """

    geral = models.BooleanField(
        "política geral",
        default=False,
        help_text="A política que vale para todo produto sem exceção própria. "
        "Existe uma, e só uma.",
    )
    produto = models.OneToOneField(
        Produto,
        on_delete=models.CASCADE,
        related_name="politica_cobranca",
        null=True,
        blank=True,
        verbose_name="produto",
        help_text="Vazio na política geral. Preenchido, é a exceção deste produto.",
    )

    dia_vencimento = models.PositiveSmallIntegerField(
        "dia do vencimento",
        default=15,
        validators=[
            MinValueValidator(DIA_VENCIMENTO_MIN),
            MaxValueValidator(DIA_VENCIMENTO_MAX),
        ],
        help_text=f"Dia do mês em que TODA parcela vence ({DIA_VENCIMENTO_MIN} a "
        f"{DIA_VENCIMENTO_MAX}). Para em 28 porque 29, 30 e 31 não existem em "
        "todo mês, e o ponto de fixar o dia é ter uma data única de cobrança.",
    )
    primeiro_vencimento = models.CharField(
        "primeira parcela",
        max_length=20,
        choices=PrimeiroVencimento.choices,
        default=PrimeiroVencimento.MES_SEGUINTE,
    )
    entrada_prazo_dias = models.PositiveSmallIntegerField(
        "prazo da entrada (dias)",
        default=0,
        validators=[MaxValueValidator(ENTRADA_PRAZO_MAX)],
        help_text="0 = a entrada é paga no dia da venda (o padrão). Acima disso, "
        "a data da entrada abre com este tanto de dias à frente.",
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "política de cobrança"
        verbose_name_plural = "políticas de cobrança"
        ordering = ("-geral", "produto__categoria", "produto__ordem", "id")
        constraints = [
            # Uma única política geral. `fields=["geral"]` com a condição faz um
            # índice PARCIAL: só as linhas com geral=True disputam a unicidade,
            # então as exceções por produto (geral=False) convivem à vontade.
            models.UniqueConstraint(
                fields=["geral"],
                condition=models.Q(geral=True),
                name="uma_unica_politica_geral",
            ),
            # As duas metades: geral não tem produto, exceção tem. Sem isto, uma
            # linha com os dois (ou nenhum) passaria despercebida e o front leria
            # uma política que não é de ninguém.
            models.CheckConstraint(
                name="politica_geral_ou_de_produto",
                condition=models.Q(geral=True, produto__isnull=True)
                | models.Q(geral=False, produto__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        alvo = self.produto.nome if self.produto else "todos os produtos"
        return f"dia {self.dia_vencimento} · {alvo}"

    @classmethod
    def geral_atual(cls) -> "PoliticaCobranca":
        """A política do Lobby. Cria com os padrões se ainda não existir.

        `get_or_create` e não `get`: um banco sem a linha (ambiente novo, banco
        de teste montado à mão) não pode derrubar o `GET /api/catalogo`, que é a
        chamada de todo consultor abrindo o app.
        """
        politica, _ = cls.objects.get_or_create(geral=True, defaults={"produto": None})
        return politica

    def como_dicionario(self) -> dict:
        return {
            "dia_vencimento": self.dia_vencimento,
            "primeiro_vencimento": self.primeiro_vencimento,
            "entrada_prazo_dias": self.entrada_prazo_dias,
        }


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


class Vigencia(models.Model):
    """Um valor que valeu por um período — a resposta para "quanto custava em março".

    O QUE ISTO ACRESCENTA AO `PublicacaoCatalogo`

    A publicação registra um EVENTO: "em 06/08 às 09:31, Mathias mudou o ELITE PRO
    de 11.500 para 12.997". Para saber quanto custava numa data, é preciso ler os
    eventos em ordem e reconstruir — o que ninguém faz olhando uma tela, e que não
    dá para consultar de fora.

    Esta tabela registra o ESTADO: "12.997 valeu de 06/08 até hoje". Uma consulta
    responde a pergunta, e a linha do tempo da tela sai daqui direto.

    POR QUE NÃO HÁ CHAVE ESTRANGEIRA PARA `Produto`

    Histórico não pode depender de linha viva. Se o produto for renomeado, o
    registro tem que continuar dizendo o nome que ele tinha na época — senão a
    trilha mente sobre o passado, que é a única coisa que ela existe para contar.
    Por isso `chave` é o slug (documentado como imutável depois de publicado) e
    `rotulo` é uma FOTOGRAFIA do nome no momento do registro.
    """

    class Campo(models.TextChoices):
        MENSALIDADE = "mensalidade", "Mensalidade"
        VALOR = "valor", "Valor à vista"
        VALOR_REFERENCIA = "valor_referencia", "Valor de referência"
        VIGENCIA_MESES = "vigencia_meses", "Vigência (meses)"
        DIA_VENCIMENTO = "dia_vencimento", "Dia do vencimento"
        PRIMEIRO_VENCIMENTO = "primeiro_vencimento", "Primeira parcela"
        ENTRADA_PRAZO_DIAS = "entrada_prazo_dias", "Prazo da entrada (dias)"

    # 'geral' para a política do Lobby; o slug do produto para o resto.
    CHAVE_GERAL = "geral"

    chave = models.CharField(
        "chave",
        max_length=40,
        db_index=True,
        help_text="O slug do produto, ou 'geral' para a política de cobrança.",
    )
    rotulo = models.CharField(
        "rótulo",
        max_length=120,
        help_text="Como o alvo se chamava quando o registro foi criado. "
        "Fotografia, não referência: renomear o produto não reescreve o passado.",
    )
    campo = models.CharField("campo", max_length=30, choices=Campo.choices)
    valor = models.CharField(
        "valor",
        max_length=40,
        help_text="Texto, porque a mesma tabela guarda dinheiro, dia do mês e "
        "escolha de regra. Quem lê sabe o tipo pelo `campo`.",
    )

    vigente_de = models.DateTimeField("vigente de", db_index=True)
    vigente_ate = models.DateTimeField(
        "vigente até",
        null=True,
        blank=True,
        help_text="Nulo = é o valor que está valendo agora.",
    )

    autor = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vigencias",
        verbose_name="autor",
    )
    autor_email = models.EmailField(
        "e-mail do autor",
        blank=True,
        default="",
        help_text="Cópia no momento do registro — a conta pode ser desativada "
        "depois, e o histórico não pode perder quem assinou.",
    )

    class Meta:
        verbose_name = "vigência de valor"
        verbose_name_plural = "vigências de valor"
        ordering = ("-vigente_de", "chave", "campo")
        constraints = [
            # No máximo um valor ABERTO por campo. É a invariante da tabela: com
            # dois, "quanto custava em março" passa a ter duas respostas e a
            # trilha deixa de servir para o que foi feita.
            models.UniqueConstraint(
                fields=["chave", "campo"],
                condition=models.Q(vigente_ate__isnull=True),
                name="um_unico_valor_vigente_por_campo",
            ),
        ]
        indexes = [models.Index(fields=["chave", "campo", "-vigente_de"])]

    def __str__(self) -> str:
        ate = "agora" if self.vigente_ate is None else f"{self.vigente_ate:%d/%m/%Y}"
        return f"{self.rotulo} · {self.get_campo_display()}: {self.valor} ({self.vigente_de:%d/%m/%Y} → {ate})"

    @property
    def vigente(self) -> bool:
        return self.vigente_ate is None
