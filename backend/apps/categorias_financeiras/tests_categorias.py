"""
Categorias financeiras: a fonte ainda desligada, a sincronização, a rota pública
e o laço.

Mesma guarda dos testes de departamento: cada falha da fonte deixa a lista do
banco exatamente como estava. E uma só daqui: antes da primeira sincronização
boa, a rota pública diz `sincronizado_em: null`, que é o que esconde o campo no
lobby enquanto a API não for ligada.
"""
import io
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .fonte import CategoriaDaFonte, FonteIndisponivel, listar_categorias
from .models import CategoriaFinanceira
from .servicos import ListagemVazia, sincronizar

COMANDO = "apps.categorias_financeiras.management.commands.sincronizar_categorias_financeiras"
ROTA = "/api/categorias-financeiras"


def cat(codigo, descricao, ativo=True):
    return CategoriaDaFonte(codigo=codigo, descricao=descricao, ativo=ativo)


class FonteTest(TestCase):
    def test_sem_api_ligada_a_fonte_esta_indisponivel(self):
        with self.assertRaises(FonteIndisponivel):
            listar_categorias()


class SincronizacaoTest(TestCase):
    LISTA = [
        cat("1.01", "Consultoria"),
        cat("1.02", "Eventos"),
        cat("1.03", "Patrocínio", ativo=False),
    ]

    def setUp(self):
        self.ontem = timezone.now() - timedelta(days=1)
        sincronizar(self.LISTA, agora=self.ontem)

    def estado(self):
        return {c.codigo: (c.descricao, c.ativo) for c in CategoriaFinanceira.objects.all()}

    def test_primeira_sincronizacao_so_conta_as_ativas_como_novas(self):
        CategoriaFinanceira.objects.all().delete()
        resultado = sincronizar(self.LISTA)

        self.assertEqual(resultado.novos, ["Consultoria", "Eventos"])
        self.assertEqual(resultado.ativos, 2)
        self.assertEqual(CategoriaFinanceira.objects.count(), 3)

    def test_sem_mudanca_so_anda_o_visto_em(self):
        alterado_antes = CategoriaFinanceira.objects.get(codigo="1.01").alterado_em
        resultado = sincronizar(self.LISTA)

        self.assertFalse(resultado.mudou)
        self.assertIn("sem mudança", str(resultado))
        categoria = CategoriaFinanceira.objects.get(codigo="1.01")
        self.assertGreater(categoria.visto_em, self.ontem)
        self.assertEqual(categoria.alterado_em, alterado_antes)

    def test_renomeada_na_fonte_e_renomeada_aqui(self):
        resultado = sincronizar([cat("1.01", "Consultoria Elite"), *self.LISTA[1:]])

        self.assertEqual(resultado.renomeados, ["Consultoria → Consultoria Elite"])
        self.assertEqual(CategoriaFinanceira.objects.get(codigo="1.01").descricao, "Consultoria Elite")

    def test_inativada_na_fonte_fica_inativa_sem_ser_apagada(self):
        resultado = sincronizar([self.LISTA[0], cat("1.02", "Eventos", ativo=False), self.LISTA[2]])

        self.assertEqual(resultado.desativados, ["Eventos"])
        self.assertFalse(CategoriaFinanceira.objects.get(codigo="1.02").ativo)
        self.assertEqual(CategoriaFinanceira.objects.count(), 3)

    def test_quem_sai_da_listagem_e_desativada_e_guarda_quando_foi_vista(self):
        resultado = sincronizar([self.LISTA[0], self.LISTA[2]])

        self.assertEqual(resultado.desativados, ["Eventos (saiu da listagem)"])
        categoria = CategoriaFinanceira.objects.get(codigo="1.02")
        self.assertFalse(categoria.ativo)
        self.assertEqual(categoria.visto_em, self.ontem)

    def test_reativada_volta_na_mesma_linha(self):
        resultado = sincronizar([*self.LISTA[:2], cat("1.03", "Patrocínio", ativo=True)])

        self.assertEqual(resultado.reativados, ["Patrocínio"])
        self.assertTrue(CategoriaFinanceira.objects.get(codigo="1.03").ativo)
        self.assertEqual(CategoriaFinanceira.objects.count(), 3)

    def test_listagem_vazia_e_falha_e_nao_mexe_em_nada(self):
        antes = self.estado()
        with self.assertRaises(ListagemVazia):
            sincronizar([])
        self.assertEqual(self.estado(), antes)


class RotaPublicaTest(TestCase):
    def setUp(self):
        self.cliente = APIClient()

    def ler(self):
        return self.cliente.get(ROTA)

    def test_antes_da_primeira_sincronizacao_diz_que_nunca_sincronizou(self):
        """É o `sincronizado_em: null` que esconde o campo no lobby."""
        resposta = self.ler()
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["categorias"], [])
        self.assertEqual(corpo["modo"], "lista")
        self.assertIsNone(corpo["sincronizado_em"])

    def test_anonimo_le_so_as_ativas_em_ordem_alfabetica(self):
        sincronizar([cat("1.02", "Eventos"), cat("1.01", "Consultoria"), cat("1.03", "Patrocínio", ativo=False)])
        resposta = self.ler()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Cache-Control"], "no-store")
        corpo = resposta.json()
        self.assertEqual(
            corpo["categorias"],
            [{"codigo": "1.01", "descricao": "Consultoria"}, {"codigo": "1.02", "descricao": "Eventos"}],
        )
        self.assertIsNotNone(corpo["sincronizado_em"])

    def test_versao_so_muda_quando_o_consultor_veria_diferenca(self):
        lista = [cat("1.01", "Consultoria"), cat("1.02", "Eventos")]
        sincronizar(lista)
        primeira = self.ler().json()["versao"]

        sincronizar(lista)
        self.assertEqual(self.ler().json()["versao"], primeira)

        sincronizar([cat("1.01", "Consultoria")])
        self.assertNotEqual(self.ler().json()["versao"], primeira)

    def test_nao_ha_escrita(self):
        self.assertEqual(self.cliente.post(ROTA, {}, format="json").status_code, 405)


class ComandoTest(TestCase):
    LISTA = [cat("1.01", "Consultoria"), cat("1.02", "Eventos")]

    def test_uma_rodada_sincroniza_e_diz_quantas_ativas(self):
        saida = io.StringIO()
        with mock.patch(f"{COMANDO}.listar_categorias", return_value=self.LISTA):
            call_command("sincronizar_categorias_financeiras", stdout=saida)

        self.assertIn("2 ativas", saida.getvalue())
        self.assertEqual(CategoriaFinanceira.objects.filter(ativo=True).count(), 2)

    def test_sem_api_ligada_a_rodada_falha_e_diz_por_que(self):
        """O estado de produção até a API chegar: o container loga e segue."""
        erro = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("sincronizar_categorias_financeiras", stdout=io.StringIO(), stderr=erro)

        self.assertIn("ainda não foi ligada", erro.getvalue())
        self.assertFalse(CategoriaFinanceira.objects.exists())

    def test_rodada_unica_que_falha_nao_mexe_na_lista(self):
        sincronizar(self.LISTA)
        with mock.patch(f"{COMANDO}.listar_categorias", side_effect=FonteIndisponivel("fora do ar")):
            with self.assertRaises(CommandError):
                call_command(
                    "sincronizar_categorias_financeiras", stdout=io.StringIO(), stderr=io.StringIO()
                )
        self.assertEqual(CategoriaFinanceira.objects.filter(ativo=True).count(), 2)

    def test_o_laco_sobrevive_a_uma_rodada_que_estoura(self):
        class Parar(Exception):
            pass

        with (
            mock.patch(f"{COMANDO}.listar_categorias", side_effect=[RuntimeError("inesperado"), self.LISTA]) as listar,
            mock.patch(f"{COMANDO}.time.sleep", side_effect=[None, Parar()]) as dormir,
            mock.patch(f"{COMANDO}.connections.close_all"),
            self.assertLogs("apps.categorias_financeiras", level="ERROR"),
        ):
            with self.assertRaises(Parar):
                call_command(
                    "sincronizar_categorias_financeiras",
                    "--a-cada",
                    "3600",
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

        self.assertEqual(listar.call_count, 2)
        dormir.assert_called_with(3600)
        self.assertEqual(CategoriaFinanceira.objects.count(), 2)

    def test_intervalo_curto_demais_e_recusado(self):
        with self.assertRaises(CommandError):
            call_command("sincronizar_categorias_financeiras", "--a-cada", "5", stdout=io.StringIO())
