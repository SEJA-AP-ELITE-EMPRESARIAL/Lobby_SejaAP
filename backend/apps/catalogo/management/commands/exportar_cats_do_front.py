"""
Imprime o `CATS` do `index.html` a partir do banco.

O PROBLEMA QUE ISTO RESOLVE

O `index.html` carrega o catálogo do `GET /api/catalogo`, mas guarda uma cópia
literal dele no próprio arquivo (`let CATS = [...]`) para o caso de a API não
responder — é o que mantém o consultor cotando em campo com o backend fora
(`boot()`, e o comentário longo acima de `CATS`). Essa cópia é escrita à mão.

Criar produto no /django-admin/ não a atualiza. Nada avisa: o produto novo
aparece normalmente na tela, e o fallback só entrega a lista velha no dia em que
a API cai — que é justamente o dia em que ninguém vai investigar por quê.

Transcrever à mão um produto novo para lá é onde erra: um zero a mais na
mensalidade, uma sigla trocada. Este comando gera o bloco pronto:

    docker exec lobby-backend python manage.py exportar_cats_do_front

E cola-se a saída sobre o array `CATS` do `index.html`. O que sai daqui passa
pelo MESMO serializer que serve a API (`serializa_catalogo`), então o fallback
não pode divergir do que o front receberia online — que é todo o ponto dele.

O comando não escreve no arquivo de propósito: mexer no `index.html` é deploy do
front (`docs/06_DEPLOY.md`), e deploy é decisão de quem está olhando, não efeito
colateral de um comando de leitura.
"""
import json
import sys

from django.core.management.base import BaseCommand

from apps.catalogo.models import Categoria
from apps.catalogo.serializers import serializa_catalogo

# A ordem e a quebra de linha do `index.html` de hoje: identificação numa linha,
# `desc` na seguinte, produtos em bloco. Seguir o formato do arquivo mantém o diff
# do deploy legível — sem isto, colar esta saída reescreveria linhas que não
# mudaram de valor, só de posição.
ORDEM_CATEGORIA = ["id", "name", "icon", "color", "flow", "sigla", "locked"]
ORDEM_PRODUTO = [
    "id", "name", "sigla", "desc", "duration",
    "monthly", "price", "recurring", "vigencia", "icon", "cobranca",
]


def _ordena(dados: dict, ordem: list[str]) -> dict:
    """Reordena as chaves conhecidas; o que for novo vai no fim, nunca some.

    O "nunca some" é o que importa: se alguém acrescentar um campo ao serializer
    e esquecer desta lista, ele sai no fim do objeto em vez de desaparecer do
    fallback — e um fallback sem o campo novo é uma tela quebrada só quando a
    API cai.
    """
    conhecidas = {k: dados[k] for k in ordem if k in dados}
    conhecidas.update({k: v for k, v in dados.items() if k not in ordem})
    return conhecidas


def _js(valor) -> str:
    """Literal JS de um valor.

    O caso de dicionário existe por causa do `cobranca` do produto (a exceção de
    data). Sem ele, `str(dict)` produziria o repr do Python — `{'dia_vencimento':
    5}`, com aspas simples nas CHAVES — dentro de uma string. O fallback
    continuaria carregando (o front cai na política geral quando não entende o
    campo), mas carregaria com a data errada, em silêncio.
    """
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float)):
        return json.dumps(valor)
    if isinstance(valor, dict):
        return _objeto_js(valor)
    if isinstance(valor, (list, tuple)):
        return "[" + ", ".join(_js(item) for item in valor) + "]"
    texto = str(valor).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{texto}'"


def _objeto_js(dados: dict) -> str:
    return "{ " + ", ".join(f"{k}: {_js(v)}" for k, v in dados.items()) + " }"


class Command(BaseCommand):
    help = "Imprime o array CATS (fallback offline do index.html) a partir do banco."

    def handle(self, *args, **opcoes):
        # "ELITE PREPARAÇÃO" tem que sair com o Ç e o Ã. Rodando no container o
        # padrão já é UTF-8, mas quem executa no Windows e redireciona para um
        # arquivo pega a página de código do console — e o resultado é
        # "PREPARA??O" colado no arquivo que os consultores enxergam quando a API
        # está fora. Falhar aqui é silencioso: o comando "funciona".
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):  # stdout substituído (testes, pipe exótico)
            pass

        catalogo = serializa_catalogo(Categoria.objects.prefetch_related("produtos").all())

        linhas = ["let CATS = ["]
        for categoria in catalogo:
            produtos = categoria.pop("products")
            descricao = categoria.pop("desc", "")
            identificacao = _ordena(categoria, ORDEM_CATEGORIA)

            linhas.append(
                "  { " + ", ".join(f"{k}: {_js(v)}" for k, v in identificacao.items()) + ","
            )
            linhas.append(f"    desc: {_js(descricao)},")
            if not produtos:
                # A APN não tem produtos e nunca vai ter: emitir o bloco vazio em
                # duas linhas só polui o arquivo.
                linhas.append("    products: [] },")
                continue
            linhas.append("    products: [")
            for produto in produtos:
                linhas.append("      " + _objeto_js(_ordena(produto, ORDEM_PRODUTO)) + ",")
            linhas.append("    ] },")
        linhas.append("];")

        self.stdout.write("\n".join(linhas))
        self.stderr.write(
            self.style.WARNING(
                "\nCole isto sobre o array `CATS` do index.html. Ele é só a rede de "
                "segurança para quando a API não responde — o preço do dia a dia "
                "continua saindo do banco."
            )
        )
