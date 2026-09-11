"""
A sincronização com o Omie e a leitura que o front consome.

A VERSÃO DA LISTA

O Lobby fica aberto horas na mesma janela. O front reconsulta a lista de tempos
em tempos e compara `versao` com a que tem: é assim que ele descobre que um
departamento entrou, saiu ou mudou de nome sem recarregar a página — o que
apagaria a venda em andamento, que só existe no estado do React.

A versão é um hash do que o consultor VÊ (código, nome e posição dos ativos), e
não da hora da sincronização. Sincronizar de hora em hora sem mudança nenhuma não
pode fazer toda tela aberta achar que a lista mudou.
"""
import hashlib
import json
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import Departamento
from .omie import DepartamentoOmie


class ListagemVazia(Exception):
    """O Omie respondeu, mas sem departamento nenhum."""


@dataclass
class Resultado:
    ativos: int = 0
    novos: list[str] = field(default_factory=list)
    renomeados: list[str] = field(default_factory=list)
    desativados: list[str] = field(default_factory=list)
    reativados: list[str] = field(default_factory=list)

    @property
    def mudou(self) -> bool:
        return bool(self.novos or self.renomeados or self.desativados or self.reativados)

    def __str__(self) -> str:
        partes = [f"{self.ativos} ativos"]
        for rotulo, itens in (
            ("novos", self.novos),
            ("renomeados", self.renomeados),
            ("desativados", self.desativados),
            ("reativados", self.reativados),
        ):
            if itens:
                partes.append(f"{rotulo}: {', '.join(itens)}")
        if not self.mudou:
            partes.append("sem mudança")
        return "; ".join(partes)


def sincronizar(listados: list[DepartamentoOmie], *, agora=None) -> Resultado:
    """Aplica a listagem do Omie ao banco, numa transação.

    Quem o Omie lista é criado ou atualizado; quem ele deixou de listar é
    desativado (nunca apagado — ver `models.py`).
    """
    if not listados:
        # Não é "todos foram removidos": é o Omie respondendo vazio, que na
        # prática é falha. Desativar tudo aqui tiraria o dropdown de todo
        # consultor por causa de uma resposta ruim.
        raise ListagemVazia(
            "O Omie não listou departamento nenhum; a lista do banco ficou como estava."
        )

    agora = agora or timezone.now()
    resultado = Resultado()
    do_omie = {d.codigo: d for d in listados}

    with transaction.atomic():
        existentes = {d.codigo: d for d in Departamento.objects.select_for_update()}

        for codigo, item in do_omie.items():
            atual = existentes.get(codigo)
            if atual is None:
                Departamento.objects.create(
                    codigo=codigo,
                    descricao=item.descricao,
                    estrutura=item.estrutura,
                    ativo=item.ativo,
                    visto_em=agora,
                )
                # Nascer inativo não muda nada na tela do consultor.
                if item.ativo:
                    resultado.novos.append(item.descricao)
                continue

            if item.ativo and not atual.ativo:
                resultado.reativados.append(item.descricao)
            elif atual.ativo and not item.ativo:
                resultado.desativados.append(atual.descricao)
            elif item.ativo and atual.descricao != item.descricao:
                resultado.renomeados.append(f"{atual.descricao} → {item.descricao}")

            novo = {"descricao": item.descricao, "estrutura": item.estrutura, "ativo": item.ativo}
            campos = ["visto_em"]
            if any(getattr(atual, nome) != valor for nome, valor in novo.items()):
                for nome, valor in novo.items():
                    setattr(atual, nome, valor)
                # `alterado_em` só anda quando algo mudou de verdade; o
                # `visto_em` anda em toda rodada.
                campos += [*novo, "alterado_em"]
            atual.visto_em = agora
            atual.save(update_fields=campos)

        for codigo, atual in existentes.items():
            if codigo not in do_omie and atual.ativo:
                # `visto_em` fica onde estava: é a última vez que o Omie o listou.
                atual.ativo = False
                atual.save(update_fields=["ativo", "alterado_em"])
                resultado.desativados.append(f"{atual.descricao} (saiu da listagem)")

    resultado.ativos = Departamento.objects.filter(ativo=True).count()
    return resultado


def ativos():
    return Departamento.objects.filter(ativo=True).order_by("estrutura", "descricao", "codigo")


def versao(departamentos) -> str:
    chave = [[d.codigo, d.descricao, d.estrutura] for d in departamentos]
    return hashlib.sha256(json.dumps(chave, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def ultima_sincronizacao():
    """Momento da última rodada bem-sucedida, ou None se nunca houve.

    Toda rodada que dá certo carimba `visto_em` em tudo que o Omie listou, então
    o maior `visto_em` É a última rodada boa. Rodada que falha não carimba nada.
    """
    return Departamento.objects.aggregate(ultima=Max("visto_em"))["ultima"]


def estado_publico() -> dict:
    lista = list(ativos())
    ultima = ultima_sincronizacao()
    return {
        "versao": versao(lista),
        "sincronizado_em": ultima.isoformat() if ultima else None,
        "departamentos": [
            {"codigo": d.codigo, "descricao": d.descricao, "estrutura": d.estrutura}
            for d in lista
        ],
    }
