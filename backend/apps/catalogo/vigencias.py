"""
Manutenção da linha do tempo de valores e datas.

A regra é uma só e está inteira em `registrar`: mudar um valor **fecha** a
vigência aberta e **abre** outra. Nada é sobrescrito, nada é apagado.

DUAS DECISÕES QUE VALEM EXPLICAÇÃO

1. **Valor igual não gera registro.** Publicar sem mexer em nada, ou salvar duas
   vezes o mesmo número, não pode picotar a linha do tempo em pedaços idênticos —
   a tela ficaria ilegível e a pergunta "desde quando custa isso" passaria a ter
   a resposta errada (a data do último clique, não a da última mudança).

2. **O fechamento e a abertura usam o MESMO instante.** Se cada um pegasse o seu
   `timezone.now()`, existiria uma janela de microssegundos sem valor nenhum —
   e uma consulta "quanto valia em T" cairia no vazio. Por isso `quando` é
   calculado uma vez e passado adiante.

Quem chama: o serviço de publicação (`servicos.py`) e o de política de cobrança.
Escrever direto na tabela, fora daqui, é o jeito de furar a invariante de "um
único valor aberto por campo" — que o banco recusa, mas com um erro que não
explica nada.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import PoliticaCobranca, Produto, Vigencia


def texto(valor) -> str:
    """Forma canônica do valor guardado.

    Dinheiro sai com duas casas SEMPRE (`12997.00`, nunca `12997`): a comparação
    com o registro anterior é textual, e `Decimal("12997")` contra
    `Decimal("12997.00")` viraria uma mudança que não houve.
    """
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    if valor is None:
        return ""
    return str(valor)


@transaction.atomic
def registrar(chave, rotulo, campo, valor, *, autor=None, quando=None) -> Vigencia | None:
    """Fecha a vigência aberta deste campo e abre outra. `None` se nada mudou."""
    valor = texto(valor)
    quando = quando or timezone.now()

    aberta = (
        Vigencia.objects.select_for_update()
        .filter(chave=chave, campo=campo, vigente_ate__isnull=True)
        .first()
    )
    if aberta is not None:
        if aberta.valor == valor:
            return None
        aberta.vigente_ate = quando
        aberta.save(update_fields=["vigente_ate"])

    return Vigencia.objects.create(
        chave=chave,
        rotulo=rotulo,
        campo=campo,
        valor=valor,
        vigente_de=quando,
        autor=autor if (autor is not None and autor.is_authenticated) else None,
        autor_email=getattr(autor, "email", "") or "",
    )


def registrar_produto(produto: Produto, *, autor=None, quando=None) -> list[Vigencia]:
    """Registra o que mudou nos valores de um produto.

    Emite só o que faz sentido para o tipo: produto recorrente não tem "valor à
    vista", e uma linha vazia dele na linha do tempo seria ruído.
    """
    quando = quando or timezone.now()
    campos = (
        [
            (Vigencia.Campo.MENSALIDADE, produto.mensalidade),
            (Vigencia.Campo.VIGENCIA_MESES, produto.vigencia_meses),
        ]
        if produto.recorrente
        else [(Vigencia.Campo.VALOR, produto.valor)]
    )
    registros = [
        registrar(produto.slug, produto.nome, campo, valor, autor=autor, quando=quando)
        for campo, valor in campos
    ]
    return [r for r in registros if r is not None]


def registrar_categoria(categoria, *, autor=None, quando=None) -> list[Vigencia]:
    """Registra o valor de referência de uma categoria de fluxo próprio (a APN).

    A `chave` é o slug da CATEGORIA, no mesmo espaço de nomes dos produtos. Não há
    colisão hoje — 'apn' não é slug de produto nenhum — e o campo é exclusivo
    (`valor_referencia`), então nem a restrição de "um valor aberto por campo"
    confundiria os dois. Se um dia existir um produto com o mesmo slug de uma
    categoria, a linha do tempo dos dois aparece junta na tela; é o preço de não
    carregar um prefixo em toda consulta.
    """
    quando = quando or timezone.now()
    registro = registrar(
        categoria.slug,
        categoria.nome,
        Vigencia.Campo.VALOR_REFERENCIA,
        categoria.valor_referencia,
        autor=autor,
        quando=quando,
    )
    return [registro] if registro else []


def registrar_politica(
    politica: PoliticaCobranca, *, autor=None, quando=None
) -> list[Vigencia]:
    """Registra o que mudou numa política de cobrança (geral ou de um produto)."""
    quando = quando or timezone.now()
    chave = Vigencia.CHAVE_GERAL if politica.geral else politica.produto.slug
    rotulo = (
        "Política de cobrança"
        if politica.geral
        else f"{politica.produto.nome} · cobrança"
    )
    registros = [
        registrar(chave, rotulo, campo, valor, autor=autor, quando=quando)
        for campo, valor in (
            (Vigencia.Campo.DIA_VENCIMENTO, politica.dia_vencimento),
            (Vigencia.Campo.PRIMEIRO_VENCIMENTO, politica.primeiro_vencimento),
            (Vigencia.Campo.ENTRADA_PRAZO_DIAS, politica.entrada_prazo_dias),
        )
    ]
    return [r for r in registros if r is not None]


def encerrar(chave, *, autor=None, quando=None) -> int:
    """Fecha tudo que estiver aberto numa chave. Usado quando uma exceção de
    produto é removida: a regra dele volta a ser a geral, e a linha do tempo
    precisa dizer até quando a exceção valeu."""
    quando = quando or timezone.now()
    return Vigencia.objects.filter(chave=chave, vigente_ate__isnull=True).exclude(
        campo__in=[
            Vigencia.Campo.MENSALIDADE,
            Vigencia.Campo.VALOR,
            Vigencia.Campo.VIGENCIA_MESES,
        ]
    ).update(vigente_ate=quando)


def linha_do_tempo(chave=None, campo=None, limite=200) -> list[Vigencia]:
    """A trilha para a tela, do mais recente para o mais antigo."""
    consulta = Vigencia.objects.all()
    if chave:
        consulta = consulta.filter(chave=chave)
    if campo:
        consulta = consulta.filter(campo=campo)
    return list(consulta[:limite])


def vigente_em(chave, campo, quando):
    """O valor que valia num instante — a pergunta que motivou a tabela.

    Devolve o `Vigencia`, ou `None` se naquele momento não havia registro (antes
    da primeira publicação, por exemplo).
    """
    return (
        Vigencia.objects.filter(chave=chave, campo=campo, vigente_de__lte=quando)
        .filter(Q(vigente_ate__isnull=True) | Q(vigente_ate__gt=quando))
        .order_by("-vigente_de")
        .first()
    )
