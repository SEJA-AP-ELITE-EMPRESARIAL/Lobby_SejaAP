"""
O histórico unificado do Admin Lobby — `GET /api/integracao/historico`.

Uma lista só, da mudança mais recente para a mais antiga, juntando o que até
aqui morava em cinco lugares (ADR-002 §13.3, item 5):

    área                   de onde vem
    catalogo               PublicacaoCatalogo — e as vigências de preço de antes
                           da TSK-1059 que não têm publicação (o PATCH de produto
                           não gravava uma)
    cobranca               Vigencia dos campos da regra de cobrança: a geral, a
                           de cada categoria, as exceções por produto e a
                           remoção delas (com quem removeu)
    categorias             Vigencia da trava de categoria (liberar/tirar do Lobby)
    departamento           ConfiguracaoDepartamento
    categoria_financeira   ConfiguracaoCategoriaFinanceira
    dh_avaliacao           ConfiguracaoValores

Cada item é um EVENTO — "fulano mudou isto, quando" — com as linhas de → para
já escritas para a tela. O "de" vem do registro anterior da mesma coisa; quando
não há anterior (a primeira gravação, uma exceção criada), a linha diz só o
"para".

Filtros: `area`, `produto` (slug), `categoria` (slug), `autor` (e-mail).
Paginação: `pagina` (1, 2, …) e `por_pagina` (até 200).

POR QUE EM PYTHON, E NÃO NUM UNION NO BANCO

São seis formatos de linha, e o "de → para" de cada um depende do registro
anterior da mesma coisa. O volume é o de uma tela de administração — centenas
de mudanças por ano —, e juntar em memória é o que mantém cada fonte legível.
As publicações vêm sem o campo `catalogo` (o catálogo inteiro por linha).
"""
import bisect
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from apps.catalogo.models import PrimeiroVencimento, Produto, PublicacaoCatalogo, Vigencia
from apps.catalogo.registro import reais
from apps.catalogo.travas import SIM, rotulo_da_trava
from apps.categorias_financeiras.models import ConfiguracaoCategoriaFinanceira
from apps.departamentos.models import ConfiguracaoDepartamento
from apps.dh_avaliacao.models import VALORES_INICIAIS, ConfiguracaoValores, Modalidade

AREAS = {
    "catalogo": "Catálogo",
    "cobranca": "Cobrança",
    "categorias": "Categorias no Lobby",
    "departamento": "Departamento",
    "categoria_financeira": "Categoria financeira",
    "dh_avaliacao": "DH - Avaliação",
}

POR_PAGINA_PADRAO = 50
POR_PAGINA_MAX = 200

CAMPOS_DE_COBRANCA = {
    Vigencia.Campo.DIA_VENCIMENTO: "dia do vencimento",
    Vigencia.Campo.PRIMEIRO_VENCIMENTO: "primeira parcela",
    Vigencia.Campo.ENTRADA_PRAZO_DIAS: "prazo da entrada",
    Vigencia.Campo.DIA_FIXO: "dia do vencimento fixo",
}
CAMPOS_DE_PRECO = {
    Vigencia.Campo.MENSALIDADE: "mensalidade",
    Vigencia.Campo.VALOR: "valor à vista",
    Vigencia.Campo.VIGENCIA_MESES: "vigência",
    Vigencia.Campo.VALOR_REFERENCIA: "valor de referência",
}
# Uma vigência de preço "tem publicação" quando a mesma pessoa publicou, até este
# tanto depois dela, uma publicação que toca aquele produto: no PUT do catálogo a
# vigência é carimbada no início da transação e a publicação, no fim.
JANELA_DA_PUBLICACAO = timedelta(seconds=60)
# Sem gravação anterior, o que valia era o padrão de cada configuração.
LISTA_COMPLETA = "lista completa"


class FiltroInvalido(Exception):
    pass


# ----- formatação -------------------------------------------------------------


def _valor(campo, texto) -> str:
    if texto is None:
        return "—"
    if campo in (Vigencia.Campo.MENSALIDADE, Vigencia.Campo.VALOR, Vigencia.Campo.VALOR_REFERENCIA):
        try:
            return reais(Decimal(texto)) if texto != "" else "—"
        except InvalidOperation:
            return texto
    if campo == Vigencia.Campo.VIGENCIA_MESES:
        return f"{texto} meses"
    if campo == Vigencia.Campo.DIA_VENCIMENTO:
        return f"dia {texto}"
    if campo == Vigencia.Campo.PRIMEIRO_VENCIMENTO:
        return {
            PrimeiroVencimento.MES_SEGUINTE: "mês seguinte",
            PrimeiroVencimento.PROXIMO: "próximo dia do vencimento",
        }.get(texto, texto)
    if campo == Vigencia.Campo.ENTRADA_PRAZO_DIAS:
        return "no dia da venda" if texto in ("0", "") else f"{texto} dias"
    if campo == Vigencia.Campo.TRAVADA:
        return rotulo_da_trava(texto)
    if campo == Vigencia.Campo.DIA_FIXO:
        return "ligado" if texto == SIM else "desligado (parcelas no dia da venda)"
    return texto


def _de_para(rotulo, campo, anterior, atual) -> str:
    if anterior is None:
        return f"{rotulo}: {_valor(campo, atual)}"
    return f"{rotulo}: {_valor(campo, anterior)} → {_valor(campo, atual)}"


def _nome(rotulo: str) -> str:
    """'ELITE PRO · cobrança' → 'ELITE PRO'; a semente da 0015 diz que é cópia."""
    return rotulo.split(" · ")[0].replace(" (copiada da regra geral)", "")


def _evento(id_, area, quando, autor, titulo, alteracoes, categoria=None, produto=None):
    return {
        "id": id_,
        "area": area,
        "quando": quando,
        "autor": autor or None,
        "titulo": titulo,
        "alteracoes": alteracoes,
        "categoria": categoria,
        "produto": produto,
    }


# ----- fontes -----------------------------------------------------------------


def _categorias_dos_produtos() -> dict:
    """slug do produto → slug da categoria, para os slugs que não se repetem.

    A `Vigencia` guarda só o slug do produto (o histórico não depende de linha
    viva). Para o filtro por categoria achar o preço e a exceção de cobrança de
    um produto, a categoria sai daqui; slug repetido entre categorias fica sem.
    """
    vistos = defaultdict(set)
    for slug, categoria in Produto.objects.values_list("slug", "categoria__slug"):
        vistos[slug].add(categoria)
    return {slug: next(iter(cats)) for slug, cats in vistos.items() if len(cats) == 1}


def _publicacoes(derivados):
    """As publicações. As de antes da TSK-1059 não têm `alvos`; para elas valem
    os derivados das vigências de preço que elas cobrem (`_precos_sem_publicacao`)."""
    eventos = []
    for p in PublicacaoCatalogo.objects.only("id", "publicado_em", "autor_email", "resumo", "alvos"):
        alvos = [a for a in p.alvos.split("|") if a] or sorted(derivados.get(p.pk, ()))
        produtos = [a.split("/", 1)[1] for a in alvos if "/" in a]
        categorias = sorted({a.split("/", 1)[0] for a in alvos} - {"?"})
        evento = _evento(
            f"publicacao:{p.pk}",
            "catalogo",
            p.publicado_em,
            p.autor_email,
            "Catálogo publicado",
            p.resumo.splitlines() if p.resumo else [],
            categoria=categorias[0] if len(categorias) == 1 else None,
            produto=produtos[0] if len(produtos) == 1 else None,
        )
        evento["_alvos"] = alvos
        eventos.append(evento)
    return eventos


def _anterior(linhas_da_chave, vigencia):
    """A vigência que esta substituiu: a mesma chave e campo, fechada no instante
    em que esta abriu."""
    for outra in linhas_da_chave:
        if outra.vigente_ate == vigencia.vigente_de and outra.pk != vigencia.pk:
            return outra
    return None


def _semente_do_dia_fixo(v, anterior) -> bool:
    """O `dia_fixo` que a migration 0017 abriu em toda regra que já existia.

    Não é evento: o dia sempre foi fixo até a TSK-1077, e a semente existe só
    para a primeira mudança de verdade dizer "ligado → desligado". Mostrada,
    ela poria uma linha nova em eventos antigos (ou um evento sem autor no dia
    do deploy) sem nada ter mudado.
    """
    return v.campo == Vigencia.Campo.DIA_FIXO and anterior is None and not v.autor_email


def _vigencias(campos, area, titulo_de, categoria_de, *, cobertas=None):
    """Agrupa as vigências em eventos: mesma chave, mesmo instante, mesma pessoa.

    `cobertas` (só no catálogo) diz quais aberturas já aparecem numa publicação.
    `categoria_de` resolve a categoria das chaves que são slug de produto.
    """
    linhas = list(Vigencia.objects.filter(campo__in=list(campos)).order_by("vigente_de", "id"))
    por_chave_campo = defaultdict(list)
    for v in linhas:
        por_chave_campo[(v.chave, v.campo)].append(v)

    grupos = defaultdict(list)
    remocoes = defaultdict(list)
    for v in linhas:
        if cobertas is not None and cobertas(v):
            continue
        grupos[(v.chave, v.vigente_de, v.autor_email)].append(v)
        if area == "cobranca" and v.vigente_ate is not None:
            sucessora = any(
                o.vigente_de == v.vigente_ate for o in por_chave_campo[(v.chave, v.campo)]
            )
            if not sucessora:
                # Fechou sem abrir outra: a exceção foi removida.
                remocoes[(v.chave, v.vigente_ate, v.encerrado_por_email)].append(v)

    eventos = []
    for (chave, quando, autor), grupo in grupos.items():
        alteracoes, primeira = [], True
        anteriores = {v.pk: _anterior(por_chave_campo[(v.chave, v.campo)], v) for v in grupo}
        grupo = [v for v in grupo if not _semente_do_dia_fixo(v, anteriores[v.pk])]
        if not grupo:
            continue
        for v in grupo:
            anterior = anteriores[v.pk]
            primeira = primeira and anterior is None
            alteracoes.append(
                _de_para(campos[v.campo], v.campo, anterior.valor if anterior else None, v.valor)
            )
        titulo, categoria, produto = titulo_de(chave, grupo[0], primeira=primeira and not autor)
        if produto and not categoria:
            categoria = categoria_de.get(produto)
        eventos.append(
            _evento(f"vigencia:{grupo[0].pk}", area, quando, autor, titulo, alteracoes, categoria, produto)
        )
    for (chave, quando, autor), grupo in remocoes.items():
        nome = _nome(grupo[0].rotulo)
        eventos.append(
            _evento(
                f"remocao:{grupo[0].pk}",
                area,
                quando,
                autor,
                f"Exceção de cobrança removida · {nome}",
                [f"{nome} volta a seguir a regra da categoria"],
                categoria_de.get(chave),
                chave,
            )
        )
    return eventos


def _titulo_da_cobranca(chave, vigencia, primeira=False):
    # `primeira`: a semente de uma migration (sem autor e sem registro anterior)
    # — o estado em que a coisa estava, não uma mudança de alguém.
    if chave == Vigencia.CHAVE_GERAL:
        sufixo = ": situação inicial" if primeira else ""
        return f"Regra geral de cobrança (início de toda categoria){sufixo}", None, None
    if chave.startswith(Vigencia.PREFIXO_CATEGORIA):
        slug = chave[len(Vigencia.PREFIXO_CATEGORIA):]
        if primeira and "copiada da regra geral" in vigencia.rotulo:
            return f"Regra de cobrança · {_nome(vigencia.rotulo)} (copiada da regra geral)", slug, None
        return f"Regra de cobrança · {_nome(vigencia.rotulo)}", slug, None
    return f"Exceção de cobrança · {_nome(vigencia.rotulo)}", None, chave


def _titulo_da_trava(chave, vigencia, primeira=False):
    slug = chave[len(Vigencia.PREFIXO_CATEGORIA):]
    if primeira:
        # A semente da 0016: o estado em que a categoria estava, não uma mudança.
        return f"{vigencia.rotulo}: situação inicial", slug, None
    acao = "tirada do Lobby" if vigencia.valor == SIM else "liberada no Lobby"
    return f"{vigencia.rotulo} {acao}", slug, None


def _titulo_do_preco(chave, vigencia, primeira=False):
    nome = _nome(vigencia.rotulo)
    sufixo = ": valor inicial" if primeira else ""
    if vigencia.campo == Vigencia.Campo.VALOR_REFERENCIA:
        return f"Valor de referência · {nome}{sufixo}", chave, None
    return f"Preço · {nome}{sufixo}", None, chave


def _precos_sem_publicacao(categoria_de, derivados):
    """As vigências de preço que nenhuma publicação conta.

    Desde a TSK-1059 toda mudança de preço tem publicação, com `alvos`. Antes
    dela, o PATCH de produto mudava preço sem publicação — e essas mudanças só
    existem na `Vigencia`. Para não contar duas vezes a que tem as duas, uma
    vigência é "coberta" quando a mesma pessoa publicou logo depois dela uma
    publicação que toca aquele alvo: pelos `alvos` (as de agora) ou, nas de
    antes, pelo nome do produto no resumo, que o PUT escrevia como "NOME: de → para".

    Quando quem cobre é uma publicação de antes, o alvo vai para `derivados`, e
    é por ele que aquela publicação passa no filtro por produto e por categoria.
    """
    publicadas = defaultdict(list)
    for pk, email, quando, alvos, resumo in PublicacaoCatalogo.objects.values_list(
        "id", "autor_email", "publicado_em", "alvos", "resumo"
    ):
        publicadas[email].append((quando, pk, alvos, resumo))
    for lista in publicadas.values():
        lista.sort(key=lambda p: p[0])

    def coberta(v):
        lista = publicadas.get(v.autor_email, [])
        i = bisect.bisect_left([p[0] for p in lista], v.vigente_de)
        for quando, pk, alvos, resumo in lista[i:]:
            if quando - v.vigente_de > JANELA_DA_PUBLICACAO:
                break
            if alvos:
                if f"/{v.chave}|" in alvos or f"|{v.chave}|" in alvos:
                    return True
            elif _resumo_antigo_conta(resumo, v):
                if v.campo == Vigencia.Campo.VALOR_REFERENCIA:
                    derivados[pk].add(v.chave)
                else:
                    derivados[pk].add(f"{categoria_de.get(v.chave, '?')}/{v.chave}")
                return True
        return False

    return _vigencias(CAMPOS_DE_PRECO, "catalogo", _titulo_do_preco, categoria_de, cobertas=coberta)


def _resumo_antigo_conta(resumo, v) -> bool:
    """Se o resumo de uma publicação de antes da TSK conta ESTA mudança.

    O PUT antigo escrevia uma linha por mudança, "NOME: de → para" (ou "NOME (valor
    de referência): de → para"), com o valor em duas casas — a mesma forma do
    `valor` da vigência. Conferir o nome E o valor resultante evita esconder um
    PATCH antigo (sem publicação) atrás de um PUT do mesmo produto, logo depois,
    para outro valor.
    """
    nome, para = _nome(v.rotulo), (v.valor or "None")
    return any(
        (linha.startswith(f"{nome}: ") or linha.startswith(f"{nome} ("))
        and linha.endswith(f"→ {para}")
        for linha in resumo.splitlines()
    )


def _catalogo(categoria_de):
    derivados = defaultdict(set)
    precos = _precos_sem_publicacao(categoria_de, derivados)  # preenche `derivados`
    return _publicacoes(derivados) + precos


def _escolha(modo, descricao, rotulo_fixo):
    return f"{rotulo_fixo}: {descricao}" if modo == "fixo" else "lista completa"


def _configuracoes_de_departamento():
    linhas = list(
        ConfiguracaoDepartamento.objects.select_related("categoria", "departamento").order_by("criado_em", "id")
    )
    ultimo = {}
    eventos = []
    for c in linhas:
        chave = c.categoria_id
        anterior = ultimo.get(chave)
        ultimo[chave] = c
        if c.copiada_de_id:
            # A cópia da migration: a decisão aparece uma vez só, na original.
            continue
        atual = _escolha(c.modo, getattr(c.departamento, "descricao", ""), "fixo")
        de = (
            _escolha(anterior.modo, getattr(anterior.departamento, "descricao", ""), "fixo")
            if anterior
            else LISTA_COMPLETA  # sem gravação, valia a lista completa
        )
        alvo = c.categoria.nome if c.categoria_id else "Elite e APN"
        eventos.append(
            _evento(
                f"departamento:{c.pk}",
                "departamento",
                c.criado_em,
                c.autor_email,
                f"Departamento da venda · {alvo}",
                [f"{alvo}: {de} → {atual}"],
                c.categoria.slug if c.categoria_id else None,
            )
        )
    return eventos


def _configuracoes_de_categoria_financeira():
    eventos, anterior = [], None
    for c in ConfiguracaoCategoriaFinanceira.objects.select_related("categoria").order_by("criado_em", "id"):
        atual = _escolha(c.modo, getattr(c.categoria, "descricao", ""), "fixa")
        de = (
            _escolha(anterior.modo, getattr(anterior.categoria, "descricao", ""), "fixa")
            if anterior
            else LISTA_COMPLETA
        )
        anterior = c
        eventos.append(
            _evento(
                f"categoria_financeira:{c.pk}",
                "categoria_financeira",
                c.criado_em,
                c.autor_email,
                "Categoria financeira · APN",
                [f"APN: {de} → {atual}"],
                "apn",
            )
        )
    return eventos


def _configuracoes_de_valores():
    produto = Produto.objects.filter(fluxo="dh_avaliacao").select_related("categoria").first()
    eventos, anterior = [], None
    for c in ConfiguracaoValores.objects.order_by("criado_em", "id"):
        alteracoes = []
        for modalidade in Modalidade:
            valor, fixo = c.da_modalidade(modalidade)
            atual = f"{reais(valor)} {'fixo' if fixo else 'livre'}"
            # Sem gravação anterior valiam os valores iniciais, livres.
            valor_antes, fixo_antes = (
                anterior.da_modalidade(modalidade) if anterior else (VALORES_INICIAIS[modalidade], False)
            )
            if (valor_antes, fixo_antes) != (valor, fixo):
                alteracoes.append(
                    f"{modalidade.label}: {reais(valor_antes)} {'fixo' if fixo_antes else 'livre'} → {atual}"
                )
        anterior = c
        eventos.append(
            _evento(
                f"dh_avaliacao:{c.pk}",
                "dh_avaliacao",
                c.criado_em,
                c.autor_email,
                "Valores do DH - Avaliação",
                alteracoes,
                produto.categoria.slug if produto else None,
                produto.slug if produto else None,
            )
        )
    return eventos


FONTES = {
    "catalogo": _catalogo,
    "cobranca": lambda c: _vigencias(CAMPOS_DE_COBRANCA, "cobranca", _titulo_da_cobranca, c),
    "categorias": lambda c: _vigencias({Vigencia.Campo.TRAVADA: "situação"}, "categorias", _titulo_da_trava, c),
    "departamento": lambda c: _configuracoes_de_departamento(),
    "categoria_financeira": lambda c: _configuracoes_de_categoria_financeira(),
    "dh_avaliacao": lambda c: _configuracoes_de_valores(),
}


# ----- filtros e página -------------------------------------------------------


def _passa(evento, *, produto, categoria, autor):
    if autor and (evento["autor"] or "").lower() != autor:
        return False
    alvos = evento.get("_alvos")
    if produto:
        if alvos is not None:
            if not any(a.endswith("/" + produto) for a in alvos):
                return False
        elif evento["produto"] != produto:
            return False
    if categoria:
        if alvos is not None:
            if not any(a == categoria or a.startswith(categoria + "/") for a in alvos):
                return False
        elif evento["area"] == "departamento" and evento["categoria"] is None:
            # A escolha de antes da TSK-1059 valia para todas.
            pass
        elif evento["categoria"] != categoria:
            return False
    return True


def _inteiro(bruto, padrao, minimo, maximo):
    try:
        valor = int(bruto)
    except (TypeError, ValueError):
        return padrao
    return max(minimo, min(maximo, valor))


def consulta(parametros) -> dict:
    area = (parametros.get("area") or "").strip()
    if area and area not in AREAS:
        raise FiltroInvalido(f'Área desconhecida: "{area}". Use uma de: {", ".join(AREAS)}.')
    produto = (parametros.get("produto") or "").strip()
    categoria = (parametros.get("categoria") or "").strip()
    autor = (parametros.get("autor") or "").strip().lower()
    pagina = _inteiro(parametros.get("pagina"), 1, 1, 100000)
    por_pagina = _inteiro(parametros.get("por_pagina"), POR_PAGINA_PADRAO, 1, POR_PAGINA_MAX)

    categoria_de = _categorias_dos_produtos()
    eventos = []
    for nome, fonte in FONTES.items():
        if area and nome != area:
            continue
        eventos.extend(
            e for e in fonte(categoria_de) if _passa(e, produto=produto, categoria=categoria, autor=autor)
        )

    # Mais recente primeiro; o id desempata o mesmo instante de forma estável.
    eventos.sort(key=lambda e: (e["quando"], e["id"]), reverse=True)
    total = len(eventos)
    inicio = (pagina - 1) * por_pagina
    itens = eventos[inicio:inicio + por_pagina]
    for evento in itens:
        evento.pop("_alvos", None)
        evento["quando"] = evento["quando"].isoformat()
    return {
        "itens": itens,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total": total,
        "paginas": (total + por_pagina - 1) // por_pagina,
        "areas": [{"valor": valor, "rotulo": rotulo} for valor, rotulo in AREAS.items()],
    }
