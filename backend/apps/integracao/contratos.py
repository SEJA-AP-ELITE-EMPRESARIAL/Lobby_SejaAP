"""
Os contratos da integração em arquivo (ADR-002 §9).

Cada resposta da integração tem o corpo guardado em
`backend/tests/contratos/integracao/<nome>.json`, e o teste compara a resposta
real com o arquivo, campo a campo — ignorando só o que muda a cada chamada
(datas, ids, versão). O arquivo quebrando é o aviso de que o contrato mudou: o
Conecta (TSK-1060) guarda uma cópia destes arquivos, e trocar um deles é uma
decisão consciente, que aparece no diff do PR.

Pasta `contratos/` e não `fixtures/`: `fixtures/` é a convenção do Django para
dado de banco (`loaddata`), e isto é corpo HTTP.

Para regravar de propósito, depois de mudar o contrato:

    ATUALIZAR_CONTRATOS=1 python manage.py test apps.integracao

Dentro dos arquivos, só gente fictícia, com @exemplo.com.br (ADR-002 §9).
"""
import json
import os
from pathlib import Path

from django.conf import settings

PASTA = Path(settings.BASE_DIR) / "tests" / "contratos" / "integracao"

# O que muda a cada chamada. Comparado só pelo tipo (texto ou nulo). O `id`
# só quando é chave de banco ("vigencia:42"); o `id` de categoria é o slug, e
# esse é contrato.
VOLATEIS = {"quando", "publicado_em", "alterado_em", "sincronizado_em", "versao"}
# O catálogo inteiro, que depende das sementes do banco: comparado só por ser lista.
SO_O_TIPO = {"cats"}
MARCA = "<varia>"


def _normaliza(valor, chave=None):
    if chave in VOLATEIS and valor is not None:
        return MARCA
    if chave == "id" and isinstance(valor, str) and ":" in valor:
        return MARCA
    if chave in SO_O_TIPO:
        return "<lista>" if isinstance(valor, list) else valor
    if isinstance(valor, dict):
        return {k: _normaliza(v, k) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_normaliza(v) for v in valor]
    return valor


def confere(teste, corpo, nome):
    """Compara `corpo` com o arquivo `nome`.json (ou o grava, se pedido)."""
    arquivo = PASTA / f"{nome}.json"
    normalizado = _normaliza(corpo)
    if os.environ.get("ATUALIZAR_CONTRATOS") == "1":
        PASTA.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(normalizado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    teste.assertTrue(arquivo.exists(), f"Falta o contrato {arquivo}. Rode com ATUALIZAR_CONTRATOS=1.")
    esperado = json.loads(arquivo.read_text(encoding="utf-8"))
    teste.assertEqual(normalizado, esperado, f"O contrato {nome}.json mudou.")
