"""
Departamentos do Omie: o cliente, a sincronização, a rota pública e o laço.

O que estes testes guardam não é "a lista chega" — é que ela NÃO SOME. Cada falha
possível do Omie (fora do ar, chave errada, resposta vazia) tem de deixar a lista
do banco exatamente como estava, porque é ela que o consultor vê no dropdown.
"""
import io
import json
import urllib.error
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Departamento
from .omie import URL, DepartamentoOmie, OmieIndisponivel, listar_departamentos
from .servicos import ListagemVazia, sincronizar

CHAVE = "chave-de-teste"
SEGREDO = "segredo-que-nao-pode-vazar"
COMANDO = "apps.departamentos.management.commands.sincronizar_departamentos"


def dep(codigo, descricao, estrutura="001.001", ativo=True):
    return DepartamentoOmie(codigo=codigo, descricao=descricao, estrutura=estrutura, ativo=ativo)


class _Resposta:
    """O suficiente de uma resposta do `urlopen` para o `with`."""

    def __init__(self, corpo):
        self._corpo = json.dumps(corpo).encode("utf-8")

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def pagina_do_omie(departamentos, pagina=1, total_de_paginas=1):
    """Mesmo formato da resposta real do ListarDepartamentos (11/09/2026)."""
    return {
        "pagina": pagina,
        "total_de_paginas": total_de_paginas,
        "registros": len(departamentos),
        "total_de_registros": len(departamentos),
        "departamentos": departamentos,
    }


@override_settings(OMIE_APP_KEY=CHAVE, OMIE_APP_SECRET=SEGREDO)
class ClienteOmieTest(TestCase):
    def test_traduz_a_listagem_e_o_campo_inativo(self):
        resposta = pagina_do_omie(
            [
                {"codigo": "6423300904", "descricao": "1.Consultoria", "estrutura": "001.001", "inativo": "N"},
                {"codigo": "6438080666", "descricao": "Inteligência B.I", "estrutura": "001.005", "inativo": "S"},
            ]
        )
        with mock.patch("urllib.request.urlopen", return_value=_Resposta(resposta)) as urlopen:
            departamentos = listar_departamentos()

        self.assertEqual(
            departamentos,
            [
                dep("6423300904", "1.Consultoria", "001.001", True),
                dep("6438080666", "Inteligência B.I", "001.005", False),
            ],
        )
        pedido = urlopen.call_args.args[0]
        corpo = json.loads(pedido.data)
        self.assertEqual(pedido.get_method(), "POST")
        self.assertEqual(corpo["call"], "ListarDepartamentos")
        self.assertEqual(corpo["app_key"], CHAVE)
        self.assertEqual(corpo["app_secret"], SEGREDO)

    def test_percorre_todas_as_paginas(self):
        respostas = [
            _Resposta(pagina_do_omie([{"codigo": "1", "descricao": "A", "inativo": "N"}], 1, 2)),
            _Resposta(pagina_do_omie([{"codigo": "2", "descricao": "B", "inativo": "N"}], 2, 2)),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=respostas) as urlopen:
            departamentos = listar_departamentos()

        self.assertEqual([d.codigo for d in departamentos], ["1", "2"])
        paginas = [json.loads(c.args[0].data)["param"][0]["pagina"] for c in urlopen.call_args_list]
        self.assertEqual(paginas, [1, 2])

    def test_item_sem_codigo_ou_descricao_e_ignorado(self):
        resposta = pagina_do_omie(
            [{"codigo": "", "descricao": "Sem código"}, {"codigo": "9", "descricao": " "}, {"codigo": "1", "descricao": "Ok"}]
        )
        with mock.patch("urllib.request.urlopen", return_value=_Resposta(resposta)):
            with self.assertLogs("apps.departamentos.omie", level="WARNING"):
                departamentos = listar_departamentos()
        self.assertEqual([d.codigo for d in departamentos], ["1"])

    def test_faultstring_vira_indisponivel_sem_vazar_a_chave(self):
        """Inclusive a listagem vazia, que o Omie devolve como falha."""
        corpo = json.dumps(
            {"faultstring": "ERROR: Não existem registros para a página [1]!", "faultcode": "SOAP-ENV:Client-5113"}
        ).encode("utf-8")
        erro = urllib.error.HTTPError(URL, 500, "Internal Server Error", {}, io.BytesIO(corpo))
        with mock.patch("urllib.request.urlopen", side_effect=erro):
            with self.assertRaises(OmieIndisponivel) as contexto:
                listar_departamentos()

        mensagem = str(contexto.exception)
        self.assertIn("500", mensagem)
        self.assertIn("Não existem registros", mensagem)
        self.assertNotIn(SEGREDO, mensagem)
        self.assertNotIn(CHAVE, mensagem)

    def test_sem_conexao_vira_indisponivel(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(OmieIndisponivel):
                listar_departamentos()

    @override_settings(OMIE_APP_KEY="", OMIE_APP_SECRET="")
    def test_sem_credencial_nem_chama_o_omie(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(OmieIndisponivel):
                listar_departamentos()
        urlopen.assert_not_called()


class SincronizacaoTest(TestCase):
    LISTA = [
        dep("1", "1.Consultoria", "001.001"),
        dep("2", "2.Comercial", "001.009"),
        dep("3", "Copa", "001.017", ativo=False),
    ]

    def setUp(self):
        self.ontem = timezone.now() - timedelta(days=1)
        sincronizar(self.LISTA, agora=self.ontem)

    def estado(self):
        return {d.codigo: (d.descricao, d.estrutura, d.ativo) for d in Departamento.objects.all()}

    def test_primeira_sincronizacao_so_conta_os_ativos_como_novos(self):
        Departamento.objects.all().delete()
        resultado = sincronizar(self.LISTA)

        self.assertEqual(resultado.novos, ["1.Consultoria", "2.Comercial"])
        self.assertEqual(resultado.ativos, 2)
        # O inativo também vira linha: se um dia for reativado, volta nela.
        self.assertEqual(Departamento.objects.count(), 3)

    def test_sem_mudanca_so_anda_o_visto_em(self):
        alterado_antes = Departamento.objects.get(codigo="1").alterado_em
        resultado = sincronizar(self.LISTA)

        self.assertFalse(resultado.mudou)
        self.assertIn("sem mudança", str(resultado))
        departamento = Departamento.objects.get(codigo="1")
        self.assertGreater(departamento.visto_em, self.ontem)
        self.assertEqual(departamento.alterado_em, alterado_antes)

    def test_renomeado_no_omie_e_renomeado_aqui(self):
        resultado = sincronizar([dep("1", "1.Consultoria Elite", "001.001"), *self.LISTA[1:]])

        self.assertEqual(resultado.renomeados, ["1.Consultoria → 1.Consultoria Elite"])
        self.assertEqual(Departamento.objects.get(codigo="1").descricao, "1.Consultoria Elite")

    def test_inativado_no_omie_fica_inativo_sem_ser_apagado(self):
        resultado = sincronizar([self.LISTA[0], dep("2", "2.Comercial", "001.009", ativo=False), self.LISTA[2]])

        self.assertEqual(resultado.desativados, ["2.Comercial"])
        self.assertFalse(Departamento.objects.get(codigo="2").ativo)
        self.assertEqual(Departamento.objects.count(), 3)

    def test_quem_sai_da_listagem_e_desativado_e_guarda_quando_foi_visto(self):
        resultado = sincronizar([self.LISTA[0], self.LISTA[2]])

        self.assertEqual(resultado.desativados, ["2.Comercial (saiu da listagem)"])
        departamento = Departamento.objects.get(codigo="2")
        self.assertFalse(departamento.ativo)
        self.assertEqual(departamento.visto_em, self.ontem)

    def test_reativado_volta_na_mesma_linha(self):
        resultado = sincronizar([*self.LISTA[:2], dep("3", "Copa", "001.017", ativo=True)])

        self.assertEqual(resultado.reativados, ["Copa"])
        self.assertTrue(Departamento.objects.get(codigo="3").ativo)
        self.assertEqual(Departamento.objects.count(), 3)

    def test_listagem_vazia_e_falha_e_nao_mexe_em_nada(self):
        antes = self.estado()
        with self.assertRaises(ListagemVazia):
            sincronizar([])
        self.assertEqual(self.estado(), antes)


class RotaPublicaTest(TestCase):
    def setUp(self):
        self.cliente = APIClient()

    def ler(self):
        return self.cliente.get("/api/departamentos")

    def test_antes_da_primeira_sincronizacao_responde_lista_vazia(self):
        resposta = self.ler()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["departamentos"], [])
        self.assertIsNone(resposta.json()["sincronizado_em"])

    def test_anonimo_le_so_os_ativos_na_ordem_da_estrutura(self):
        sincronizar(
            [
                dep("2", "2.Comercial", "001.009"),
                dep("1", "1.Consultoria", "001.001"),
                dep("3", "Copa", "001.017", ativo=False),
            ]
        )
        resposta = self.ler()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Cache-Control"], "no-store")
        corpo = resposta.json()
        self.assertEqual(
            corpo["departamentos"],
            [
                {"codigo": "1", "descricao": "1.Consultoria", "estrutura": "001.001"},
                {"codigo": "2", "descricao": "2.Comercial", "estrutura": "001.009"},
            ],
        )
        self.assertIsNotNone(corpo["sincronizado_em"])

    def test_versao_so_muda_quando_o_consultor_veria_diferenca(self):
        """A tela aberta compara a versão. Sincronizar sem mudança não pode
        fazer todo mundo achar que a lista mudou."""
        lista = [dep("1", "1.Consultoria", "001.001"), dep("2", "2.Comercial", "001.009")]
        sincronizar(lista)
        primeira = self.ler().json()["versao"]

        sincronizar(lista)
        self.assertEqual(self.ler().json()["versao"], primeira)

        sincronizar([dep("1", "1.Consultoria", "001.001")])
        self.assertNotEqual(self.ler().json()["versao"], primeira)

    def test_nao_ha_escrita(self):
        self.assertEqual(self.cliente.post("/api/departamentos", {}, format="json").status_code, 405)


class ComandoTest(TestCase):
    LISTA = [dep("1", "1.Consultoria", "001.001"), dep("2", "2.Comercial", "001.009")]

    def test_uma_rodada_sincroniza_e_diz_quantos_ativos(self):
        saida = io.StringIO()
        with mock.patch(f"{COMANDO}.listar_departamentos", return_value=self.LISTA):
            call_command("sincronizar_departamentos", stdout=saida)

        self.assertIn("2 ativos", saida.getvalue())
        self.assertEqual(Departamento.objects.filter(ativo=True).count(), 2)

    def test_rodada_unica_que_falha_sai_com_erro_e_nao_mexe_na_lista(self):
        sincronizar(self.LISTA)
        erro = io.StringIO()
        with mock.patch(
            f"{COMANDO}.listar_departamentos",
            side_effect=OmieIndisponivel("Omie respondeu 403: chave de acesso inválida"),
        ):
            with self.assertRaises(CommandError):
                call_command("sincronizar_departamentos", stdout=io.StringIO(), stderr=erro)

        self.assertIn("chave de acesso inválida", erro.getvalue())
        self.assertEqual(Departamento.objects.filter(ativo=True).count(), 2)

    def test_o_laco_sobrevive_a_uma_rodada_que_estoura(self):
        class Parar(Exception):
            pass

        with (
            mock.patch(f"{COMANDO}.listar_departamentos", side_effect=[RuntimeError("inesperado"), self.LISTA]) as listar,
            mock.patch(f"{COMANDO}.time.sleep", side_effect=[None, Parar()]) as dormir,
            mock.patch(f"{COMANDO}.connections.close_all"),
            self.assertLogs("apps.departamentos", level="ERROR"),
        ):
            with self.assertRaises(Parar):
                call_command(
                    "sincronizar_departamentos", "--a-cada", "3600", stdout=io.StringIO(), stderr=io.StringIO()
                )

        self.assertEqual(listar.call_count, 2)
        dormir.assert_called_with(3600)
        self.assertEqual(Departamento.objects.count(), 2)

    def test_intervalo_curto_demais_e_recusado(self):
        with self.assertRaises(CommandError):
            call_command("sincronizar_departamentos", "--a-cada", "5", stdout=io.StringIO())
