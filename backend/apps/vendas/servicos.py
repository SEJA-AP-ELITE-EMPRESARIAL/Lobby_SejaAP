"""
Emissão e validação do comprovante de venda.

A regra central, em uma frase: **o servidor só assina valores que ele mesmo
consegue justificar** — ou porque batem com a tabela de preços do banco, ou
porque alguém com permissão autorizou a diferença.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.catalogo.models import Categoria, Produto, arredonda
from apps.contas.models import papel_de

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


def _valores_normalizados(dados: dict) -> dict:
    """O subconjunto do payload que é assinado.

    Só o que representa dinheiro e identifica o produto. Nome do cliente, CNPJ e
    datas não entram: mudá-los não cria desconto, e incluí-los faria a validação
    falhar por qualquer correção de cadastro feita entre a emissão e o envio.
    """
    cronograma = dados.get("cronograma") or []
    if not isinstance(cronograma, list):
        raise VendaRecusada("Cronograma inválido.")

    return {
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


def emitir(dados: dict, *, usuario=None, ip=None) -> ComprovanteVenda:
    """Assina os valores de uma venda, se eles se sustentarem.

    `usuario` é quem autorizou a negociação, quando houve. Vem do JWT que o
    modal de autorização obteve — ou seja, alguém que provou identidade no
    Conecta ID e tem papel para isso.
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


@transaction.atomic
def validar(token: str, dados: dict) -> dict:
    """Confere e CONSOME o comprovante. Chamado pelo n8n, nunca pelo navegador.

    Devolve um dicionário com o veredito e o contexto para auditoria. Nunca
    levanta por motivo de negócio: o n8n precisa de uma resposta que ele consiga
    ramificar, não de um 500.
    """
    def recusa(motivo, **extra):
        return {"valido": False, "motivo": motivo, **extra}

    if not token or "." not in str(token):
        return recusa("comprovante_ausente")

    identificador, _, assinatura = str(token).partition(".")
    try:
        comprovante = ComprovanteVenda.objects.select_for_update().get(pk=identificador)
    except (ComprovanteVenda.DoesNotExist, ValueError, TypeError):
        return recusa("comprovante_desconhecido")

    if not comprovante.assinatura_confere(assinatura):
        return recusa("assinatura_invalida")
    if comprovante.consumido:
        # Reuso. Ou é replay, ou o n8n reprocessou — vale distinguir no log.
        return recusa("comprovante_ja_usado", consumido_em=comprovante.consumido_em.isoformat())
    if comprovante.expirado:
        return recusa("comprovante_expirado")

    try:
        valores = _valores_normalizados(dados)
    except VendaRecusada as erro:
        return recusa("valores_invalidos", detalhe=str(erro))

    if hash_dos_valores(valores) != comprovante.hash_valores:
        # O payload que chegou ao n8n não é o que foi assinado.
        return recusa("valores_adulterados")

    comprovante.consumido_em = timezone.now()
    comprovante.save(update_fields=["consumido_em"])

    return {
        "valido": True,
        "protocolo": comprovante.protocolo,
        "fluxo": comprovante.fluxo,
        "negociado": comprovante.negociado,
        "autorizado_por": comprovante.autorizador_email or None,
        "papel": papel_de(comprovante.autorizador) if comprovante.autorizador else None,
        "emitido_em": comprovante.emitido_em.isoformat(),
    }
