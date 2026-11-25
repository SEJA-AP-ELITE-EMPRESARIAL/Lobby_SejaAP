"""
O Casais de Alta Performance (TSK-1622): os dois valores, os documentos e a
conferência do Elite Empresarial no Conecta.

OS VALORES

Decisão do dono, 25/11/2026: R$ 39.997,00 (o preço CHEIO) ou R$ 35.000,00 (o do
ELITE EMPRESARIAL), e é esse o valor do contrato. Não são de tabela (não estão no
catálogo nem no Admin Lobby): o produto é de formulário próprio, e os dois
números moram aqui e no `index.html` (`CASAIS_VALOR_*`). O comprovante
(`apps.vendas.servicos`) não assina outro.

QUEM ESCOLHE ENTRE OS DOIS

- PJ: o Conecta, pelo CNPJ (`conecta.cliente_ativo`). Ativo → Elite; não ativo
  → cheio. SEM RESPOSTA (fora do ar, lento, 429, 503, manutenção) → cheio: o
  valor do Elite numa PJ nunca sai sem a conferência. É o que o dono decidiu, e
  ele pode mudar no aceite.
- PF: o consultor, marcando "paga o valor do Elite Empresarial".

O BALDE DO CONECTA

O teto da rota no Conecta (30 por minuto, 120 por hora) é UM para o Lobby
inteiro, e estourado ele fecha a pergunta para todos os consultores. Por isso:

- a resposta (sim ou não) fica guardada por CNPJ, `GUARDAR_A_RESPOSTA`. A tela
  pergunta quando o CNPJ fica completo, e o comprovante, minutos depois, lê a
  mesma resposta sem ir ao Conecta. A chave é o hash do CNPJ: o Redis não vira
  uma lista de CNPJs;
- depois de uma falha, nenhum processo pergunta por um tempo (o `Retry-After`
  do 429, ou `PAUSA_DEPOIS_DE_FALHA`): sem laço, e um Conecta pendurado não
  prende um worker por pergunta;
- cada pessoa tem um teto de perguntas que vão de fato ao Conecta
  (`LOBBY_CASAIS_CONSULTAS_POR_HORA`, por hora cheia), somando a tela e o
  comprovante. A resposta guardada não conta. Sem ele, um consultor digitando
  CNPJs em série (ou um script com a sessão dele) esgotaria o balde de todos.

Tudo isso no cache `default` (o Redis do Lobby em produção, falha aberta). Sem
Redis cada processo tem o seu, como os outros tetos.
"""
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

from . import conecta

logger = logging.getLogger(__name__)

VALOR_CHEIO = Decimal("39997.00")
VALOR_ELITE = Decimal("35000.00")
# `tabela` no payload e no comprovante: "elite" ou "cheio".
VALOR_DA_TABELA = {"cheio": VALOR_CHEIO, "elite": VALOR_ELITE}

# Em segundos. Curto: o status do cliente muda no Conecta, e a resposta só
# precisa durar uma venda — da tela que pergunta ao comprovante e à validação.
GUARDAR_A_RESPOSTA = 10 * 60
PAUSA_DEPOIS_DE_FALHA = 60
# O `Retry-After` vale até aqui. O teto por hora do Conecta é de janela fixa:
# mais de uma hora não é resposta dele.
PAUSA_MAXIMA = 60 * 60
JANELA_DA_PESSOA = 60 * 60

_CHAVE_DA_PAUSA = "casais:conecta:pausa"


# ----- documentos -----------------------------------------------------------

_CNPJ = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_PESOS_DO_CNPJ = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def _digito_do_cnpj(base):
    # O valor de cada caractere é o código ASCII menos 48: os dígitos valem o que
    # sempre valeram, e as letras do CNPJ alfanumérico da Receita (2026) de 17 a 42.
    pesos = _PESOS_DO_CNPJ[-len(base):]
    resto = sum((ord(c) - 48) * p for c, p in zip(base, pesos)) % 11
    return 0 if resto < 2 else 11 - resto


def normaliza_cnpj(valor):
    """O CNPJ sem máscara e em maiúsculas, ou `None` se ele não é um CNPJ.

    Vale o numérico e o alfanumérico da Receita, como no Conecta. A tela do Lobby
    só aceita dígitos hoje; quem chama a API com o outro não é recusado à toa.
    """
    if not isinstance(valor, str):
        return None
    cnpj = re.sub(r"[.\-/\s]", "", valor).upper()
    if not _CNPJ.match(cnpj) or len(set(cnpj)) == 1:
        return None
    if _digito_do_cnpj(cnpj[:12]) != int(cnpj[12]) or _digito_do_cnpj(cnpj[:13]) != int(cnpj[13]):
        return None
    return cnpj


def normaliza_cpf(valor):
    """O CPF só com os dígitos, ou `None` se ele não é um CPF."""
    if not isinstance(valor, str):
        return None
    cpf = re.sub(r"[.\-\s]", "", valor)
    if not re.fullmatch(r"\d{11}", cpf) or len(set(cpf)) == 1:
        return None
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        if (soma * 10) % 11 % 10 != int(cpf[tamanho]):
            return None
    return cpf


def reais(valor):
    """`R$ 39.997,00`, como a tela escreve."""
    inteiro, centavos = f"{Decimal(valor):.2f}".split(".")
    return f"R$ {int(inteiro):,}".replace(",", ".") + f",{centavos}"


# ----- a conferência no Conecta --------------------------------------------

@dataclass(frozen=True)
class Conferencia:
    """O que o Conecta disse do CNPJ. `cliente_ativo` `None` é "sem resposta"."""

    cliente_ativo: bool | None
    motivo: str = ""
    tentar_em: int | None = None

    @property
    def conferido(self):
        return self.cliente_ativo is not None

    @property
    def tabela(self):
        return "elite" if self.cliente_ativo else "cheio"


def _chave_da_resposta(cnpj):
    return "casais:conecta:resposta:" + hashlib.sha256(cnpj.encode()).hexdigest()[:32]


def _ler(chave):
    try:
        return cache.get(chave)
    except Exception:
        logger.warning("casais: cache indisponível ao ler", exc_info=True)
        return None


def _gravar(chave, valor, segundos):
    try:
        cache.set(chave, valor, segundos)
    except Exception:
        logger.warning("casais: cache indisponível ao gravar", exc_info=True)


def _pausa(falta):
    """Ninguém pergunta ao Conecta por um tempo depois desta falha. Devolve os segundos."""
    if falta.motivo in ("nao_configurado", "dados_invalidos"):
        # Sem Conecta configurado não há o que pausar; e o 400 é deste CNPJ, não
        # do Conecta — pausar todo mundo por ele seria punir quem não errou.
        return None
    segundos = falta.tentar_em or PAUSA_DEPOIS_DE_FALHA
    segundos = max(1, min(PAUSA_MAXIMA, int(segundos)))
    _gravar(_CHAVE_DA_PAUSA, {"ate": time.time() + segundos, "motivo": falta.motivo}, segundos)
    return segundos


def _passou_do_teto(pessoa, agora):
    """Conta uma pergunta desta pessoa. Devolve os segundos até poder de novo, ou `None`.

    Falha aberta, como os outros tetos: sem cache, a pergunta passa.
    """
    teto = getattr(settings, "LOBBY_CASAIS_CONSULTAS_POR_HORA", 20)
    janela = int(agora // JANELA_DA_PESSOA)
    chave = f"casais:conecta:pessoa:{pessoa.pk}:{janela}"
    try:
        cache.add(chave, 0, JANELA_DA_PESSOA + 60)
        usadas = cache.incr(chave)
    except Exception:
        logger.warning("casais: teto por pessoa sem cache; a pergunta passa", exc_info=True)
        return None
    if usadas <= teto:
        return None
    return int((janela + 1) * JANELA_DA_PESSOA - agora) + 1


def conferir_cnpj(cnpj, *, pessoa=None):
    """A `Conferencia` do CNPJ (já normalizado): a guardada, ou perguntada agora.

    `pessoa` é quem vende (o teto por pessoa); `None` na validação do n8n, que
    não tem pessoa e só chega depois de um comprovante. Nunca levanta.
    """
    chave = _chave_da_resposta(cnpj)
    guardada = _ler(chave)
    if guardada in ("ativo", "inativo"):
        return Conferencia(guardada == "ativo")

    agora = time.time()
    pausa = _ler(_CHAVE_DA_PAUSA)
    if isinstance(pausa, dict) and float(pausa.get("ate") or 0) > agora:
        return Conferencia(None, str(pausa.get("motivo") or "indisponivel"),
                           math.ceil(float(pausa["ate"]) - agora))

    if pessoa is not None and getattr(pessoa, "pk", None) is not None:
        espera = _passou_do_teto(pessoa, agora)
        if espera is not None:
            logger.warning("casais: %s passou do teto de perguntas ao Conecta", pessoa.pk)
            return Conferencia(None, "limite_da_pessoa", espera)

    try:
        ativo = conecta.cliente_ativo(cnpj)
    except conecta.SemResposta as falta:
        return Conferencia(None, falta.motivo, _pausa(falta))
    except Exception:
        # Defeito daqui, e não do Conecta: a venda segue no preço cheio, e o log
        # (e o GlitchTip) diz onde.
        logger.exception("casais: falha inesperada ao perguntar ao Conecta")
        return Conferencia(None, "indisponivel", None)
    _gravar(chave, "ativo" if ativo else "inativo", GUARDAR_A_RESPOSTA)
    return Conferencia(ativo)


def mensagem(conferencia):
    """A frase que a tela mostra para esta conferência."""
    if conferencia.cliente_ativo is True:
        return (
            "Cliente ativo do Elite Empresarial no Conecta: vale o valor do Elite, "
            f"{reais(VALOR_ELITE)}."
        )
    if conferencia.cliente_ativo is False:
        return (
            "O Conecta não tem este CNPJ como cliente ativo do Elite Empresarial: "
            f"vale o preço cheio, {reais(VALOR_CHEIO)}."
        )
    if conferencia.motivo == "limite_da_pessoa":
        motivo = "Você fez muitas consultas ao Conecta nesta hora"
    elif conferencia.motivo == "limite":
        motivo = "O Conecta está recebendo consultas demais agora"
    elif conferencia.motivo == "manutencao":
        motivo = "O Conecta está em manutenção"
    else:
        motivo = "Não foi possível conferir o CNPJ no Conecta agora"
    return (
        f"{motivo}. Sem a conferência, a venda fica no preço cheio, {reais(VALOR_CHEIO)}."
    )
