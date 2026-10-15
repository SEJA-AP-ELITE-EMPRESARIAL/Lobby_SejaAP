"""
Um registro só para cada mudança do catálogo (TSK-1059, ADR-002 §13.3 item 3).

Até aqui, só o `PUT /api/catalogo` gravava `PublicacaoCatalogo`. Editar um
produto pelo `PATCH` — preço, nome, sigla — mudava o que todo consultor vê sem
deixar publicação nenhuma: a `Vigencia` registrava o preço, e a mudança só de
texto não deixava rastro algum. Agora toda escrita do catálogo passa por
`grava_publicacao`, com autor, o resumo legível e os ALVOS (os produtos e
categorias tocados), que é por onde o histórico unificado filtra por produto.

O texto do resumo é para gente ler — "ELITE PRO: mensalidade R$ 9.997,00 →
R$ 10.497,00" —, e por isso ninguém deve interpretá-lo de volta. Quem precisa
filtrar usa `alvos`.
"""
from decimal import Decimal

from .models import Categoria, Produto, PublicacaoCatalogo
from .serializers import serializa_catalogo

# Rótulos dos campos do produto no resumo, na ordem em que aparecem.
ROTULOS = {
    "nome": "nome",
    "sigla": "sigla",
    "descricao": "descrição",
    "duracao": "duração",
    "icone": "ícone",
    "recorrente": "tipo",
    "mensalidade": "mensalidade",
    "vigencia_meses": "vigência",
    "valor": "valor à vista",
}
CAMPOS_DE_TEXTO = ("nome", "sigla", "descricao", "duracao", "icone")
CAMPOS_DE_PRECO = ("recorrente", "mensalidade", "vigencia_meses", "valor")


def reais(valor) -> str:
    """`R$ 10.497,00`. `—` para a ausência de valor, que não é zero."""
    if valor is None:
        return "—"
    inteiro, centavos = f"{Decimal(valor):,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def descreve(campo, valor) -> str:
    if campo in ("mensalidade", "valor", "valor_referencia"):
        return reais(valor)
    if campo == "vigencia_meses":
        return "—" if valor is None else f"{valor} meses"
    if campo == "recorrente":
        return "recorrente" if valor else "avulso"
    return f'"{valor}"' if valor not in (None, "") else "(vazio)"


def alvo_de_produto(produto: Produto) -> str:
    return f"{produto.categoria.slug}/{produto.slug}"


def fotografia(produto: Produto) -> dict:
    """Os campos do produto que o resumo compara, antes de mexer nele."""
    return {campo: getattr(produto, campo) for campo in (*CAMPOS_DE_TEXTO, *CAMPOS_DE_PRECO)}


def diferencas(nome: str, antes: dict, depois: dict) -> list[str]:
    """Uma linha por campo que mudou: `NOME: campo de → para`."""
    linhas = []
    for campo, rotulo in ROTULOS.items():
        if campo in antes and antes[campo] != depois.get(campo):
            linhas.append(
                f"{nome}: {rotulo} {descreve(campo, antes[campo])} → {descreve(campo, depois.get(campo))}"
            )
    return linhas


def grava_publicacao(*, autor, linhas, alvos) -> PublicacaoCatalogo:
    """Uma `PublicacaoCatalogo` com o catálogo como fica servido depois dela."""
    catalogo = serializa_catalogo(
        Categoria.objects.prefetch_related("produtos__politica_cobranca").all()
    )
    return PublicacaoCatalogo.objects.create(
        autor=autor if (autor is not None and getattr(autor, "is_authenticated", False)) else None,
        autor_email=getattr(autor, "email", "") or "",
        catalogo=catalogo,
        resumo="\n".join(str(linha) for linha in linhas),
        alvos=("|" + "|".join(sorted(set(alvos))) + "|") if alvos else "",
    )
