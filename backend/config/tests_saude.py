"""
`GET /saude`, o healthcheck do compose (TSK-1513).

Até a TSK-1513 o healthcheck batia no `GET /api/catalogo`; fechado o catálogo,
ele levaria 401 e o backend ficaria `unhealthy` para sempre. Estes testes seguram
o que o compose precisa da rota nova: 200 sem sessão, sem teto, 503 com o banco
fora — e nada além de "ok" no corpo.
"""
from pathlib import Path
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


class SaudeTest(TestCase):
    def test_responde_sem_sessao(self):
        resposta = APIClient().get("/saude", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"ok": True})
        self.assertEqual(resposta["Cache-Control"], "no-store")

    def test_sem_teto_a_cada_30_segundos_o_dia_inteiro(self):
        """Um teto aqui deixaria o backend `unhealthy` no meio do dia."""
        cliente = APIClient()
        codigos = {cliente.get("/saude").status_code for _ in range(50)}
        self.assertEqual(codigos, {200})

    def test_token_vencido_no_cabecalho_nao_muda_nada(self):
        resposta = APIClient().get("/saude", HTTP_AUTHORIZATION="Bearer vencido")
        self.assertEqual(resposta.status_code, 200)

    def test_banco_fora_e_503_e_nao_500(self):
        """503 derruba o healthcheck igual, sem virar um evento no GlitchTip a
        cada 30 s."""
        with mock.patch("config.saude.connection.cursor", side_effect=RuntimeError("túnel fora")):
            with self.assertLogs("config.saude", level="WARNING"):
                resposta = APIClient().get("/saude")
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json(), {"ok": False})

    def test_so_get(self):
        self.assertEqual(APIClient().post("/saude").status_code, 405)

    def test_o_healthcheck_do_compose_bate_aqui_e_nao_numa_rota_fechada(self):
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8000/saude", compose)
        self.assertNotIn("http://127.0.0.1:8000/api/", compose)
