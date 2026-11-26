"""
Emissão e validação do comprovante de venda.

A regra central, em uma frase: **o servidor só assina valores que ele mesmo
consegue justificar** — ou porque batem com a tabela de preços do banco, ou
porque alguém com permissão autorizou a diferença.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.casais.servicos import (
    VALOR_DA_TABELA as VALOR_DO_CASAIS,
    conferir_cnpj,
    elite_decidido_por,
    normaliza_cnpj,
    reais,
)
from apps.catalogo.models import Categoria, Produto, arredonda
from apps.contas.models import papel_de
from apps.dh_avaliacao.models import DA_MODALIDADE, QUANTIDADE_MAX, VALOR_UNITARIO_MAX
from apps.dh_avaliacao.servicos import em_vigor as valores_da_avaliacao

from .models import VALIDADE_MINUTOS, ComprovanteVenda, hash_dos_valores

# Um centavo de folga: o front calcula em ponto flutuante e o servidor em
# Decimal. Exigir igualdade exata reprovaria venda legítima por arredondamento —
# e o consultor não teria a menor ideia do porquê.
TOLERANCIA = Decimal("0.01")


class VendaRecusada(Exception):
    """Os valores não se sustentam. A mensagem vai para a tela do consultor."""


def _decimal(valor, campo: str) -> Decimal:
    try:
        return arredonda(Decimal(str(valor if valor is not None else 0)))
    except (InvalidOperation, ValueError, TypeError):
        raise VendaRecusada(f"Valor inválido em {campo}.")


def _bate(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCIA


def _reais(valor: Decimal) -> str:
    return "R$ " + f"{valor:.2f}".replace(".", ",")


# Teto da conversão para `int`, em casas decimais, e não regra de negócio (a
# regra é o `QUANTIDADE_MAX`, conferido adiante, com mensagem própria). Existe
# porque `int(Decimal("1e5000000"))` leva minutos: sem ele, um POST anônimo com
# uma quantidade em notação exponencial prenderia um worker do gunicorn. É o
# expoente que se olha, e não `abs(numero) > teto`: a aritmética do Decimal
# estoura (`Overflow`) antes de comparar um número desses.
INTEIRO_MAX_EXPOENTE = 9


def _inteiro(valor, campo: str) -> int:
    """Quantidade é contagem: 3, e não 2,5. `True` não é 1 aqui."""
    if isinstance(valor, bool):
        raise VendaRecusada(f"Valor inválido em {campo}.")
    try:
        numero = Decimal(str(valor if valor is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        raise VendaRecusada(f"Valor inválido em {campo}.")
    if not numero.is_finite() or numero.adjusted() > INTEIRO_MAX_EXPOENTE:
        raise VendaRecusada(f"Valor inválido em {campo}.")
    if numero != numero.to_integral_value():
        raise VendaRecusada(f"Valor inválido em {campo}.")
    return int(numero)


def _itens_normalizados(brutos) -> list[dict]:
    """Os itens do DH - Avaliação, na forma em que são assinados.

    Entram no hash porque SÃO o dinheiro desta venda: o total sozinho deixaria
    trocar a Devolutiva fixada por um Relatório mais caro, na mesma soma.
    """
    if not isinstance(brutos, list):
        raise VendaRecusada("Itens inválidos.")
    itens = []
    for bruto in brutos:
        if not isinstance(bruto, dict):
            raise VendaRecusada("Itens inválidos.")
        enviado = bruto.get("valor_unitario")
        unitario = _decimal(enviado, "valor unitário")
        if not unitario.is_finite():
            raise VendaRecusada("Valor inválido em valor unitário.")
        # Fração de centavo é recusada, e não arredondada: arredondar faria
        # 479,995 virar o 480,00 fixo no que é assinado, enquanto os itens do
        # payload seguiriam dizendo 479,995 — R$ 50 a menos em 10.000 unidades,
        # com o comprovante válido. O lobby manda sempre duas casas.
        # (`_decimal` já recusou o que não é número; ausente conta como zero.)
        if Decimal(str(enviado if enviado is not None else 0)) != unitario:
            raise VendaRecusada("Valor unitário com fração de centavo.")
        itens.append({
            "modalidade": str(bruto.get("modalidade") or ""),
            "quantidade": _inteiro(bruto.get("quantidade"), "quantidade"),
            "valor_unitario": str(unitario),
        })
    return itens


def _casais_normalizado(bruto) -> dict:
    """O bloco `casais` do Casais de Alta Performance, na forma em que é assinado.

    Entra no hash porque é ele que decide o dinheiro: a `tabela` (o valor) e, na
    PJ, o CNPJ que o Conecta conferiu. Sem o CNPJ assinado, o comprovante de um
    cliente do Elite serviria para vender, pelo valor do Elite, a outra empresa.
    Ele vai sem máscara: a tela pode mandar com ou sem.

    Na PF o documento NÃO entra (vai vazio, qualquer que seja o que veio): lá
    quem decide o valor é o consultor, e o CPF de uma pessoa não tem por que
    ficar guardado no comprovante, que guarda só o que é dinheiro.
    """
    if not isinstance(bruto, dict):
        raise VendaRecusada("Dados do contratante do Casais de Alta Performance ausentes.")
    tipo = str(bruto.get("tipo_pessoa") or "").upper()
    if tipo not in ("PF", "PJ"):
        raise VendaRecusada("Tipo de pessoa do contratante inválido.")
    documento = ""
    if tipo == "PJ":
        documento = normaliza_cnpj(bruto.get("documento"))
        if documento is None:
            raise VendaRecusada("CNPJ do contratante inválido.")
    tabela = str(bruto.get("tabela") or "")
    if tabela not in VALOR_DO_CASAIS:
        raise VendaRecusada("Tabela do Casais de Alta Performance inválida.")
    conferido = bruto.get("elite_conferido_no_conecta")
    if not isinstance(conferido, bool):
        raise VendaRecusada("Conferência do Conecta inválida.")
    return {
        "tipo_pessoa": tipo,
        "documento": documento,
        "tabela": tabela,
        "elite_conferido_no_conecta": conferido,
    }


def _valores_normalizados(dados: dict) -> dict:
    """O subconjunto do payload que é assinado.

    Só o que representa dinheiro e identifica o produto. Nome do cliente, CNPJ e
    datas não entram: mudá-los não cria desconto, e incluí-los faria a validação
    falhar por qualquer correção de cadastro feita entre a emissão e o envio.

    `itens` existe só no DH - Avaliação, e `casais` só no Casais de Alta
    Performance (lá o CNPJ entra, porque ali ele decide o valor). Nos outros
    fluxos as chaves nem aparecem: acrescentá-las a todos mudaria o hash das
    vendas de sempre, e um comprovante emitido antes do deploy seria recusado
    como adulterado depois dele.
    """
    cronograma = dados.get("cronograma") or []
    if not isinstance(cronograma, list):
        raise VendaRecusada("Cronograma inválido.")

    valores = {
        "fluxo": str(dados.get("fluxo") or ""),
        "categoria_id": str(dados.get("categoria_id") or ""),
        "produto_id": str(dados.get("produto_id") or ""),
        "negociado": bool(dados.get("negociado")),
        "valor_total": str(_decimal(dados.get("valor_total"), "valor total")),
        "valor_mensal": (
            str(_decimal(dados.get("valor_mensal"), "valor mensal"))
            if dados.get("valor_mensal") is not None
            else None
        ),
        "entrada": str(_decimal(dados.get("entrada"), "entrada")),
        "cronograma": [str(_decimal(v, "parcela")) for v in cronograma],
        "protocolo": str(dados.get("protocolo") or ""),
    }
    if valores["fluxo"] == ComprovanteVenda.Fluxo.DH_AVALIACAO:
        valores["itens"] = _itens_normalizados(dados.get("itens"))
    elif valores["fluxo"] == ComprovanteVenda.Fluxo.CASAIS:
        valores["casais"] = _casais_normalizado(dados.get("casais"))
    return valores


def _confere_soma(valores: dict):
    """Entrada + parcelas tem que fechar com o total.

    Sem isto, dava para declarar um total de tabela e distribuir centavos nas
    parcelas — o contrato diria um valor e a cobrança seria outra.
    """
    total = Decimal(valores["valor_total"])
    soma = Decimal(valores["entrada"]) + sum(
        (Decimal(p) for p in valores["cronograma"]), Decimal(0)
    )
    if not _bate(total, soma):
        raise VendaRecusada(
            f"A soma da entrada com as parcelas ({soma}) não fecha com o "
            f"valor total ({total})."
        )


def _recusa_produto_de_formulario(produto):
    """Produto de fluxo próprio não se vende pela tabela.

    O caso real: uma aba do lobby aberta antes do deploy que transformou o
    Avaliações em DH - Avaliação (TSK-1056). Ela ainda o mostra como produto de
    tabela e pede comprovante de Elite. Sem esta recusa, a conferência contra a
    tabela comparava o total com um preço que não existe (`None`) e o servidor
    respondia 500 — e a venda seguia para o webhook do Elite sem comprovante.
    Com 422, a tela para e mostra a mensagem.
    """
    if produto.de_formulario:
        raise VendaRecusada(
            f'"{produto.nome}" agora tem formulário próprio. Recarregue a página '
            "e faça a venda por ele."
        )


def _confere_contra_a_tabela(valores: dict):
    """Venda sem negociação: os valores TÊM que ser os do catálogo."""
    try:
        produto = Produto.objects.select_related("categoria").get(
            categoria__slug=valores["categoria_id"], slug=valores["produto_id"]
        )
    except Produto.DoesNotExist:
        raise VendaRecusada(
            "Produto não encontrado no catálogo. Recarregue a página e tente de novo."
        )

    _recusa_produto_de_formulario(produto)
    total = Decimal(valores["valor_total"])
    if not _bate(total, produto.preco):
        raise VendaRecusada(
            f'O valor de "{produto.nome}" não confere com a tabela. '
            "Para alterar o valor é preciso a autorização de um gerente."
        )

    if produto.recorrente:
        mensal = valores["valor_mensal"]
        if mensal is None or not _bate(Decimal(mensal), produto.mensalidade):
            raise VendaRecusada(
                f'A mensalidade de "{produto.nome}" não confere com a tabela. '
                "Para alterar o valor é preciso a autorização de um gerente."
            )


def _confere_avaliacao(valores: dict):
    """DH - Avaliação: valor livre, menos o que a diretoria fixou.

    É o caminho do valor livre da APN e do DH — o servidor não tem tabela contra
    a qual conferir o que o consultor digitou —, com uma trava a mais: a
    modalidade de valor FIXO (`apps.dh_avaliacao`) só passa com o valor fixo.
    Sem isto, a trava do lobby seria cosmética: bastaria montar o payload à mão.
    Nem a autorização de um gerente abre exceção. Fixar é decisão da diretoria
    para todas as vendas; o formulário não tem negociação.
    """
    if not Produto.objects.filter(
        categoria__slug=valores["categoria_id"],
        slug=valores["produto_id"],
        fluxo=ComprovanteVenda.Fluxo.DH_AVALIACAO,
    ).exists():
        raise VendaRecusada(
            "Produto de formulário não encontrado no catálogo. Recarregue a "
            "página e tente de novo."
        )
    if valores["negociado"]:
        raise VendaRecusada(
            "O DH - Avaliação não tem negociação: o valor livre já é digitado pelo "
            "consultor, e o fixo é da diretoria."
        )

    itens = valores["itens"]
    if not itens:
        raise VendaRecusada("Informe a quantidade de pelo menos uma modalidade.")

    vigentes = valores_da_avaliacao()
    vistas = set()
    soma = Decimal(0)
    for item in itens:
        modalidade = item["modalidade"]
        if modalidade not in vigentes:
            raise VendaRecusada(
                f'Modalidade "{modalidade}" desconhecida. Recarregue a página e tente de novo.'
            )
        rotulo = DA_MODALIDADE[modalidade]
        if modalidade in vistas:
            raise VendaRecusada(f"Há dois itens {rotulo}: junte as quantidades num só.")
        vistas.add(modalidade)

        if item["quantidade"] <= 0:
            # Quantidade zero é modalidade não vendida, e ela não vai em `itens`.
            raise VendaRecusada(f"A quantidade {rotulo} tem de ser maior que zero.")
        if item["quantidade"] > QUANTIDADE_MAX:
            raise VendaRecusada(f"A quantidade {rotulo} está fora da faixa permitida.")
        unitario = Decimal(item["valor_unitario"])
        if not (Decimal(0) < unitario <= VALOR_UNITARIO_MAX):
            raise VendaRecusada(f"O valor unitário {rotulo} está fora da faixa permitida.")
        fixo = vigentes[modalidade]
        # Igualdade exata, sem a tolerância de `_bate`: os dois lados já são
        # Decimal de duas casas, e um centavo de folga por unidade viraria R$ 100
        # abaixo do fixo numa venda de 10.000 unidades.
        if fixo["fixo"] and unitario != fixo["valor"]:
            raise VendaRecusada(
                f"O valor {rotulo} é fixado pela diretoria em {_reais(fixo['valor'])}. "
                "Volte à etapa Itens e confira o total."
            )
        soma += unitario * item["quantidade"]

    total = Decimal(valores["valor_total"])
    if not _bate(total, arredonda(soma)):
        raise VendaRecusada(
            f"A soma dos itens ({arredonda(soma)}) não fecha com o valor total ({total})."
        )
    # Mesma forma da APN: o total inteiro na entrada, sem cronograma. O que
    # parcela no cartão é a operadora, não o Lobby.
    _confere_soma(valores)


def _confere_casais_fixo(valores: dict):
    """Casais de Alta Performance: o que dá para conferir sem perguntar a ninguém.

    O valor é um dos dois fixos, e o da `tabela` que veio junto, sem negociação
    e sem cronograma (o total inteiro na entrada, como no DH - Avaliação: quem
    parcela no cartão é a operadora). E a PF nunca diz que o Conecta conferiu: lá
    quem decide o Elite é o consultor, e o Conecta não é perguntado.

    Vale na emissão e na validação do n8n (`valores_invalidos`).
    """
    casais = valores["casais"]
    if valores["negociado"]:
        raise VendaRecusada(
            "O Casais de Alta Performance não tem negociação: o valor é o cheio ou o "
            "do Elite Empresarial."
        )
    total = Decimal(valores["valor_total"])
    if total not in VALOR_DO_CASAIS.values():
        raise VendaRecusada(
            f"O valor do Casais de Alta Performance é {reais(VALOR_DO_CASAIS['cheio'])} "
            f"(preço cheio) ou {reais(VALOR_DO_CASAIS['elite'])} (Elite Empresarial)."
        )
    if total != VALOR_DO_CASAIS[casais["tabela"]]:
        raise VendaRecusada("O valor não confere com a tabela da venda (cheio ou Elite).")
    if valores["cronograma"] or Decimal(valores["entrada"]) != total:
        raise VendaRecusada("O Casais de Alta Performance vai num pagamento só, sem cronograma.")
    if casais["tipo_pessoa"] == "PF" and casais["elite_conferido_no_conecta"]:
        raise VendaRecusada("Na pessoa física o Elite Empresarial não é conferido no Conecta.")


def _confere_casais(valores: dict, vendedor=None):
    """Casais de Alta Performance: o valor fixo e, na PJ, o Conecta de novo.

    A tela mostra o que o Conecta disse (`/api/casais/elite`), mas aquilo é só o
    que ela MOSTRA: o booleano vem do navegador. Aqui o servidor pergunta de novo
    — a resposta guardada há pouco, ou o próprio Conecta — e só assina se a tela
    disser o mesmo que ele:

    - o Conecta respondeu: Elite com o CNPJ ativo, cheio com o não ativo, os dois
      conferidos. A caixa marcada à mão não vale aqui: na PJ, quem decide é ele;
    - o Conecta não respondeu, nem para a tela nem agora para o servidor: o que o
      consultor marcou (TSK-1640, decisão do dono de 26/11/2026), sem
      conferência. O navegador dizer que o Conecta estava fora não basta: é a
      pergunta DAQUI que tem de ficar sem resposta. E a validação do n8n
      pergunta outra vez (`validar`);
    - a pergunta não saiu pelo teto da própria pessoa: o cheio, sem conferência
      (`Conferencia.consultor_decide`).

    Na PF não há o que perguntar: "paga o valor do Elite Empresarial" é do
    consultor, e os dois valores passam.
    """
    if not Produto.objects.filter(
        categoria__slug=valores["categoria_id"],
        slug=valores["produto_id"],
        fluxo=ComprovanteVenda.Fluxo.CASAIS,
    ).exists():
        raise VendaRecusada(
            "Produto de formulário não encontrado no catálogo. Recarregue a "
            "página e tente de novo."
        )
    _confere_casais_fixo(valores)

    casais = valores["casais"]
    if casais["tipo_pessoa"] != "PJ":
        return
    conferencia = conferir_cnpj(casais["documento"], pessoa=vendedor)
    tela = (casais["tabela"], casais["elite_conferido_no_conecta"])
    if conferencia.conferido:
        if tela == (conferencia.tabela, True):
            return
    elif conferencia.consultor_decide:
        # As duas tabelas passam: quem marcou foi o consultor.
        if not casais["elite_conferido_no_conecta"]:
            return
    elif tela == ("cheio", False):
        return
    if conferencia.cliente_ativo is True:
        raise VendaRecusada(
            "O Conecta confirmou que este CNPJ é cliente ativo do Elite Empresarial: o "
            f"valor desta venda é {reais(VALOR_DO_CASAIS['elite'])}. Confira o valor e "
            "finalize de novo."
        )
    if conferencia.cliente_ativo is False:
        raise VendaRecusada(
            "O Conecta não tem este CNPJ como cliente ativo do Elite Empresarial: o "
            f"valor desta venda é {reais(VALOR_DO_CASAIS['cheio'])}. Confira o valor e "
            "finalize de novo."
        )
    if conferencia.consultor_decide:
        raise VendaRecusada(
            "Não foi possível conferir o CNPJ no Conecta agora: o valor desta venda é "
            "o que você marcar em \"paga o valor do Elite Empresarial\". Confira o valor "
            "e finalize de novo."
        )
    raise VendaRecusada(
        "Você fez muitas consultas ao Conecta nesta hora, e sem a conferência o valor "
        "do Elite Empresarial não sai numa empresa. A venda fica no preço cheio, "
        f"{reais(VALOR_DO_CASAIS['cheio'])}: confira o valor e finalize de novo."
    )


def emitir(dados: dict, *, usuario=None, ip=None, vendedor=None) -> ComprovanteVenda:
    """Assina os valores de uma venda, se eles se sustentarem.

    `usuario` é quem autorizou a negociação, quando houve. Vem do JWT que o
    modal de autorização obteve — ou seja, alguém que provou identidade no
    Conecta ID e tem papel para isso.

    `vendedor` é quem está logado vendendo. Só o Casais de Alta Performance o
    usa: é o balde dele que paga a pergunta ao Conecta (`apps.casais.servicos`).
    """
    valores = _valores_normalizados(dados)

    if valores["fluxo"] not in dict(ComprovanteVenda.Fluxo.choices):
        raise VendaRecusada("Fluxo de venda desconhecido.")
    if not valores["protocolo"]:
        raise VendaRecusada("Protocolo da venda ausente.")

    if valores["fluxo"] == ComprovanteVenda.Fluxo.ELITE:
        _confere_soma(valores)
        if valores["negociado"]:
            # Negociar é exatamente o ato que exige credencial. Sem isso, o
            # comprovante viraria um carimbo automático em qualquer valor.
            if usuario is None or not usuario.is_authenticated:
                raise VendaRecusada(
                    "Valores negociados exigem a autorização de um gerente ou "
                    "da diretoria."
                )
            vinculo = getattr(usuario, "vinculo_identidade", None)
            if not (vinculo and vinculo.pode_autorizar_negociacao):
                raise VendaRecusada(
                    "Esta conta não tem permissão para autorizar negociação."
                )
            # Negociado não passa pela tabela, mas também não vende produto de
            # formulário pelo cronograma do Elite (ver `_recusa_produto_de_formulario`).
            produto = Produto.objects.filter(
                categoria__slug=valores["categoria_id"], slug=valores["produto_id"]
            ).first()
            if produto is not None:
                _recusa_produto_de_formulario(produto)
        else:
            _confere_contra_a_tabela(valores)
    elif valores["fluxo"] == ComprovanteVenda.Fluxo.DH:
        # DH (Recrutamento e Seleção): também não há tabela — o valor da venda é
        # a soma dos adiantamentos do quadro de vagas, que só existe depois de o
        # cliente preencher o formulário. O que dá para conferir, e é conferido,
        # são duas coisas:
        #
        # 1. o produto existe E é mesmo de fluxo `dh` (um produto de tabela
        #    entrando por aqui estaria fugindo da conferência de preço);
        # 2. a soma fecha — as parcelas que o front declarou (uma por vaga) têm
        #    que dar o total que ele mandou. Sem isso, o resumo diria um número
        #    e o cronograma cobraria outro.
        if not Produto.objects.filter(
            categoria__slug=valores["categoria_id"],
            slug=valores["produto_id"],
            fluxo=ComprovanteVenda.Fluxo.DH,
        ).exists():
            raise VendaRecusada(
                "Produto de formulário não encontrado no catálogo. Recarregue a "
                "página e tente de novo."
            )
        _confere_soma(valores)
    elif valores["fluxo"] == ComprovanteVenda.Fluxo.DH_AVALIACAO:
        _confere_avaliacao(valores)
    elif valores["fluxo"] == ComprovanteVenda.Fluxo.CASAIS:
        _confere_casais(valores, vendedor)
    else:
        # APN: valor livre por decisão de produto (docs/08_ANDAMENTO.md) — não há
        # tabela contra a qual conferir. O comprovante aqui atesta origem, não
        # legitimidade do valor: impede forjar um payload direto no webhook, e
        # registra quem enviou. Se um dia a APN passar a exigir autorização,
        # é aqui que a regra entra.
        if not Categoria.objects.filter(
            slug=valores["categoria_id"], fluxo=ComprovanteVenda.Fluxo.APN
        ).exists():
            raise VendaRecusada("Categoria APN não encontrada.")

    return ComprovanteVenda.objects.create(
        protocolo=valores["protocolo"],
        fluxo=valores["fluxo"],
        negociado=valores["negociado"],
        valores=valores,
        hash_valores=hash_dos_valores(valores),
        autorizador=usuario if (usuario and usuario.is_authenticated) else None,
        autorizador_email=getattr(usuario, "email", "") or "",
        expira_em=timezone.now() + timedelta(minutes=VALIDADE_MINUTOS),
        ip_emissao=ip,
    )


def validar(token: str, dados: dict) -> dict:
    """Confere e CONSOME o comprovante. Chamado pelo n8n, nunca pelo navegador.

    Devolve um dicionário com o veredito e o contexto para auditoria. Nunca
    levanta por motivo de negócio: o n8n precisa de uma resposta que ele consiga
    ramificar, não de um 500.

    Os valores são lidos antes da transação, e é lá fora que o Elite de uma PJ do
    Casais de Alta Performance é reconsultado no Conecta (até alguns segundos):
    a pergunta não segura a linha do comprovante travada. A ordem dos motivos é a
    de sempre — o veredito sai todo de dentro da transação.

    E a pergunta só sai para um comprovante que passaria em todo o resto
    (`_comprovante_que_passaria`): o webhook do n8n é público, e um POST com um
    comprovante forjado, usado ou de outro CNPJ não pode gastar o balde do
    Conecta, que é um só para o Lobby inteiro.
    """
    try:
        if not isinstance(dados, dict):
            raise VendaRecusada("Valores inválidos.")
        valores = _valores_normalizados(dados)
        if valores["fluxo"] == ComprovanteVenda.Fluxo.CASAIS:
            # Um valor fora dos dois fixos nem chega à conferência do hash: o
            # motivo diz o que está errado, e não só que mudou.
            _confere_casais_fixo(valores)
        invalidos = None
    except VendaRecusada as erro:
        valores, invalidos = None, str(erro)

    ativo_agora = None
    casais = valores.get("casais") if valores else None
    if (casais and casais["tipo_pessoa"] == "PJ" and casais["tabela"] == "elite"
            and _comprovante_que_passaria(token, valores)):
        # O valor do Elite numa PJ: o servidor confere de novo. Na emissão, ou o
        # Conecta disse "ativo", ou ficou sem resposta para o servidor e o
        # consultor marcou à mão (TSK-1640); aqui vale a resposta de agora — a
        # guardada, quase sempre, já que a validação chega segundos depois, e sem
        # o teto de uma pessoa. Se o Conecta disser que o CNPJ não é ativo,
        # recusa, inclusive o marcado à mão. Sem resposta dele agora, vale a
        # emissão: o n8n não bloqueia uma venda porque o Conecta piscou (ou
        # segue fora, no caso da caixa marcada à mão).
        ativo_agora = conferir_cnpj(casais["documento"]).cliente_ativo
    return _validar_na_transacao(token, valores, invalidos, ativo_agora)


def _abre_token(token):
    """`(id, assinatura)` do token, ou `None` sem o formato `<id>.<assinatura>`."""
    if not token or "." not in str(token):
        return None
    identificador, _, assinatura = str(token).partition(".")
    return identificador, assinatura


def _comprovante_que_passaria(token, valores) -> bool:
    """O comprovante existe, confere, não foi usado nem venceu, e é destes valores.

    Lido sem trava, só para decidir se vale perguntar ao Conecta: quem decide é
    a transação, que relê tudo com a linha travada.
    """
    partes = _abre_token(token)
    if partes is None:
        return False
    try:
        comprovante = ComprovanteVenda.objects.get(pk=partes[0])
    except (ComprovanteVenda.DoesNotExist, ValueError, TypeError, ValidationError):
        return False
    return (
        comprovante.assinatura_confere(partes[1])
        and not comprovante.consumido
        and not comprovante.expirado
        and hash_dos_valores(valores) == comprovante.hash_valores
    )


@transaction.atomic
def _validar_na_transacao(token, valores, invalidos, ativo_agora) -> dict:
    def recusa(motivo, **extra):
        return {"valido": False, "motivo": motivo, **extra}

    partes = _abre_token(token)
    if partes is None:
        return recusa("comprovante_ausente")

    identificador, assinatura = partes
    try:
        comprovante = ComprovanteVenda.objects.select_for_update().get(pk=identificador)
    except (ComprovanteVenda.DoesNotExist, ValueError, TypeError, ValidationError):
        # `ValidationError`: o id que nem é UUID ("x.y"). Dava 500, e o n8n
        # mandava para Conferir o que é comprovante forjado.
        return recusa("comprovante_desconhecido")

    if not comprovante.assinatura_confere(assinatura):
        return recusa("assinatura_invalida")
    if comprovante.consumido:
        # Reuso. Ou é replay, ou o n8n reprocessou — vale distinguir no log.
        return recusa("comprovante_ja_usado", consumido_em=comprovante.consumido_em.isoformat())
    if comprovante.expirado:
        return recusa("comprovante_expirado")

    if invalidos is not None:
        return recusa("valores_invalidos", detalhe=invalidos)

    if hash_dos_valores(valores) != comprovante.hash_valores:
        # O payload que chegou ao n8n não é o que foi assinado.
        return recusa("valores_adulterados")

    if ativo_agora is False:
        return recusa("elite_nao_confirmado")

    comprovante.consumido_em = timezone.now()
    comprovante.save(update_fields=["consumido_em"])

    # No Casais, o que foi assinado sobre quem contrata e o valor volta ao n8n:
    # o topo do payload é montado no navegador, e é contra ESTE bloco que o
    # fluxo confere o contratante antes de faturar (deploy/N8N-VALIDACAO.md). Um
    # comprovante legítimo da PF a R$ 35.000 não pode virar a venda de uma PJ.
    # O `elite_decidido_por` (TSK-1640) não é guardado: sai do que foi assinado.
    casais = {}
    if comprovante.fluxo == ComprovanteVenda.Fluxo.CASAIS:
        assinado = comprovante.valores["casais"]
        casais = {
            "valor_total": comprovante.valores["valor_total"],
            "casais": {
                **assinado,
                "elite_decidido_por": elite_decidido_por(
                    assinado["tipo_pessoa"], assinado["elite_conferido_no_conecta"]
                ),
            },
        }

    return {
        **casais,
        "valido": True,
        "protocolo": comprovante.protocolo,
        "fluxo": comprovante.fluxo,
        "negociado": comprovante.negociado,
        "autorizado_por": comprovante.autorizador_email or None,
        "papel": papel_de(comprovante.autorizador) if comprovante.autorizador else None,
        "emitido_em": comprovante.emitido_em.isoformat(),
    }
