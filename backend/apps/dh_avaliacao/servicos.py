"""
A leitura que o lobby consome, a gravação da aba e a conferência do comprovante.

O FIXO É APLICADO AQUI

Quem diz que um valor está travado é `em_vigor()`, e são dois os lugares que a
leem: a rota pública (o lobby mostra o campo travado) e a emissão do comprovante
(`apps.vendas.servicos`, que recusa valor diferente do fixo). Se a regra morasse
no `index.html`, bastaria um payload montado à mão para vender a Devolutiva por
outro preço.

A VERSÃO

O lobby fica horas aberto, e reconsulta os valores como reconsulta os
departamentos. A `versao` é um hash do que o consultor VÊ (valor e trava de cada
modalidade): é por ela que a tela percebe que a diretoria fixou ou soltou um
valor, sem recarregar a página — o que apagaria a venda em andamento.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from apps.catalogo.models import arredonda
from apps.catalogo.serializers import numero

from .models import (
    DA_MODALIDADE,
    VALOR_UNITARIO_MAX,
    VALORES_INICIAIS,
    ConfiguracaoValores,
    Modalidade,
)


class ConfiguracaoInvalida(Exception):
    """Recusa de gravação da aba DH - Avaliação. A mensagem vai crua para a tela."""


def configuracao_atual() -> ConfiguracaoValores | None:
    """A última gravação da aba, ou None se a diretoria nunca a salvou."""
    return ConfiguracaoValores.objects.first()


def em_vigor(configuracao=None) -> dict:
    """{modalidade: {"valor": Decimal, "fixo": bool}} — o que vale agora.

    Sem gravação nenhuma, os valores iniciais do pedido, todos livres.
    """
    if configuracao is None:
        configuracao = configuracao_atual()
    if configuracao is None:
        return {m.value: {"valor": VALORES_INICIAIS[m], "fixo": False} for m in Modalidade}
    resultado = {}
    for m in Modalidade:
        valor, fixo = configuracao.da_modalidade(m)
        resultado[m.value] = {"valor": arredonda(valor), "fixo": bool(fixo)}
    return resultado


def _modalidades(vigentes: dict) -> list[dict]:
    """A lista que as duas rotas devolvem, na ordem do formulário do lobby."""
    return [
        {
            "id": m.value,
            "nome": m.label,
            # Número JSON de verdade, como no catálogo: o lobby soma isto.
            "valor": numero(vigentes[m.value]["valor"]),
            "fixo": vigentes[m.value]["fixo"],
        }
        for m in Modalidade
    ]


def versao(vigentes: dict) -> str:
    chave = [[m.value, f"{vigentes[m.value]['valor']:.2f}", vigentes[m.value]["fixo"]] for m in Modalidade]
    return hashlib.sha256(json.dumps(chave).encode("utf-8")).hexdigest()[:16]


def estado_publico() -> dict:
    vigentes = em_vigor()
    return {"versao": versao(vigentes), "modalidades": _modalidades(vigentes)}


def estado_configuracao() -> dict:
    """O que a aba DH - Avaliação do `/admin` precisa para abrir."""
    configuracao = configuracao_atual()
    return {
        "modalidades": _modalidades(em_vigor(configuracao)),
        # A tela mostra de onde partiu quem nunca salvou a aba.
        "valores_iniciais": {m.value: numero(VALORES_INICIAIS[m]) for m in Modalidade},
        "alterado_em": configuracao.criado_em.isoformat() if configuracao is not None else None,
        # Sem autor numa gravação que existe só se a conta foi apagada; a cópia
        # do e-mail fica. None aqui é "ninguém salvou ainda".
        "alterado_por": (configuracao.autor_email or None) if configuracao is not None else None,
    }


def _valor(bruto, modalidade) -> Decimal:
    rotulo = DA_MODALIDADE[modalidade]
    if isinstance(bruto, bool) or bruto is None:
        # `True` é 1 para o Decimal. Um booleano no lugar do valor é tela quebrada,
        # não um real.
        raise ConfiguracaoInvalida(f"Informe o valor {rotulo}.")
    try:
        valor = arredonda(Decimal(str(bruto).replace(",", ".").strip()))
    except (InvalidOperation, ValueError, TypeError):
        raise ConfiguracaoInvalida(f"Valor {rotulo} inválido.")
    if not valor.is_finite() or not (Decimal(0) < valor <= VALOR_UNITARIO_MAX):
        raise ConfiguracaoInvalida(
            f"O valor {rotulo} tem de ser maior que zero e no máximo "
            f"R$ {VALOR_UNITARIO_MAX:,.0f}.".replace(",", ".")
        )
    return valor


def salvar_configuracao(dados, *, autor) -> ConfiguracaoValores:
    """Grava a aba como uma linha nova. Nada é editado.

    Corpo: `{"modalidades": {"relatorio": {"valor": 150, "fixo": false},
    "devolutiva": {...}}}`. As duas modalidades são obrigatórias: gravar só uma
    deixaria a outra sem valor na linha que passa a valer.
    """
    if not isinstance(dados, dict) or not isinstance(dados.get("modalidades"), dict):
        raise ConfiguracaoInvalida("Configuração inválida.")
    modalidades = dados["modalidades"]

    campos = {}
    for m in Modalidade:
        item = modalidades.get(m.value)
        if not isinstance(item, dict):
            raise ConfiguracaoInvalida(f"Informe o valor {DA_MODALIDADE[m]}.")
        fixo = item.get("fixo", False)
        if not isinstance(fixo, bool):
            # "false" em texto seria verdadeiro num `bool(...)` — e travaria o
            # campo de todo consultor sem ninguém ter pedido.
            raise ConfiguracaoInvalida(f"Diga se o valor {DA_MODALIDADE[m]} é fixo ou livre.")
        campos[f"{m.value}_valor"] = _valor(item.get("valor"), m)
        campos[f"{m.value}_fixo"] = fixo

    return ConfiguracaoValores.objects.create(
        **campos,
        autor=autor if getattr(autor, "is_authenticated", False) else None,
        autor_email=getattr(autor, "email", "") or "",
    )
