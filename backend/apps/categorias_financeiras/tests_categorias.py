"""
Categorias financeiras: o cliente do Omie, a sincronização, a rota pública e o
laço.

Mesma guarda dos testes de departamento: cada falha da fonte deixa a lista do
banco exatamente como estava. E duas só daqui: o recorte (só o grupo APN do
plano de contas, sem a totalizadora) e, antes da primeira sincronização boa, a
rota pública dizendo `sincronizado_em: null`, que é o que esconde o campo no
lobby.
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

from .fonte import URL, CategoriaDaFonte, FonteIndisponivel, listar_categorias
from .models import CategoriaFinanceira
from .servicos import ListagemVazia, sincronizar

COMANDO = "apps.categorias_financeiras.management.commands.sincronizar_categorias_financeiras"
ROTA = "/api/categorias-financeiras"
CHAVE = "chave-de-teste"
SEGREDO = "segredo-que-nao-pode-vazar"


def cat(codigo, descricao, ativo=True):
    return CategoriaDaFonte(codigo=codigo, descricao=descricao, ativo=ativo)


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


def item_do_omie(codigo, descricao, *, inativa="N", totalizadora="N", nao_exibir="N", receita="S"):
    """Os campos que importam de um item real do ListarCategorias (14/10/2026)."""
    return {
        "codigo": codigo,
        "descricao": descricao,
        "descricao_padrao": descricao,
        "categoria_superior": codigo.rsplit(".", 1)[0],
        "conta_inativa": inativa,
        "conta_receita": receita,
        "conta_despesa": "N" if receita == "S" else "S",
        "totalizadora": totalizadora,
        "transferencia": "N",
        "nao_exibir": nao_exibir,
        "definida_pelo_usuario": "S",
        "natureza": "",
        "tipo_categoria": "",
    }


def pagina_do_omie(itens, pagina=1, total_de_paginas=1):
    """Mesmo formato da resposta real (14/10/2026: 319 categorias em 4 páginas de 100)."""
    return {
        "pagina": pagina,
        "total_de_paginas": total_de_paginas,
        "registros": len(itens),
        "total_de_registros": len(itens),
        "categoria_cadastro": itens,
    }


PLANO = [
    item_do_omie("1.06.95", "EAP"),
    item_do_omie("1.07.99", "APN"),
    item_do_omie("1.10", "APN", totalizadora="S"),
    item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS"),
    item_do_omie("1.10.94", "APN (Indefinido)"),
    item_do_omie("1.10.95", "APN 112 SÃO PAULO", inativa="S"),
    item_do_omie("1.10.96", "&lt;Disponível&gt;", nao_exibir="S"),
    item_do_omie("1.100.01", "Não é do grupo 1.10"),
    item_do_omie("2.10.01", "Despesa qualquer", receita="N"),
]


@override_settings(OMIE_APP_KEY=CHAVE, OMIE_APP_SECRET=SEGREDO, CATEGORIAS_FINANCEIRAS_PREFIXOS="1.10")
class ClienteOmieTest(TestCase):
    def test_so_o_grupo_apn_sem_a_totalizadora(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resposta(pagina_do_omie(PLANO))) as urlopen:
            categorias = listar_categorias()

        self.assertEqual(
            categorias,
            [
                cat("1.10.93", "APN 107 FLORIANÓPOLIS"),
                cat("1.10.94", "APN (Indefinido)"),
                cat("1.10.95", "APN 112 SÃO PAULO", ativo=False),
                # Entidade HTML desfeita, e "não exibir" no Omie conta como inativa.
                cat("1.10.96", "<Disponível>", ativo=False),
            ],
        )
        pedido = urlopen.call_args.args[0]
        corpo = json.loads(pedido.data)
        self.assertEqual(pedido.full_url, URL)
        self.assertEqual(corpo["call"], "ListarCategorias")
        self.assertEqual(corpo["app_key"], CHAVE)
        self.assertEqual(corpo["app_secret"], SEGREDO)

    @override_settings(CATEGORIAS_FINANCEIRAS_PREFIXOS=" 1.06 , 1.07. ")
    def test_mais_de_um_prefixo_pela_variavel(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resposta(pagina_do_omie(PLANO))):
            categorias = listar_categorias()
        self.assertEqual([c.codigo for c in categorias], ["1.06.95", "1.07.99"])

    def test_percorre_todas_as_paginas(self):
        respostas = [
            _Resposta(pagina_do_omie([item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS")], 1, 2)),
            _Resposta(pagina_do_omie([item_do_omie("1.10.99", "APN 108 FORTALEZA")], 2, 2)),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=respostas) as urlopen:
            categorias = listar_categorias()

        self.assertEqual([c.codigo for c in categorias], ["1.10.93", "1.10.99"])
        paginas = [json.loads(c.args[0].data)["param"][0]["pagina"] for c in urlopen.call_args_list]
        self.assertEqual(paginas, [1, 2])

    def test_pagina_que_falha_no_meio_derruba_a_rodada(self):
        """Lista parcial desativaria o resto: é falha, e o banco fica como estava."""
        respostas = [
            _Resposta(pagina_do_omie([item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS")], 1, 2)),
            urllib.error.URLError("timed out"),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=respostas):
            with self.assertRaises(FonteIndisponivel):
                listar_categorias()

    def assertRodadaFalha(self, respostas, trecho):
        with mock.patch("urllib.request.urlopen", side_effect=respostas):
            with self.assertRaises(FonteIndisponivel) as contexto:
                listar_categorias()
        self.assertIn(trecho, str(contexto.exception))

    def test_item_do_grupo_sem_descricao_derruba_a_rodada(self):
        """Pular desativaria uma turma que continua no Omie."""
        itens = [item_do_omie("1.10.93", "  "), item_do_omie("1.10.94", "APN (Indefinido)")]
        self.assertRodadaFalha([_Resposta(pagina_do_omie(itens))], "1.10.93")

    def test_segunda_pagina_com_faultstring_e_status_200(self):
        self.assertRodadaFalha(
            [
                _Resposta(pagina_do_omie([item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS")], 1, 2)),
                _Resposta({"faultstring": "ERROR: Consumo redundante", "faultcode": "SOAP-ENV:Client-6"}),
            ],
            "Consumo redundante",
        )

    def test_segunda_pagina_sem_categoria_cadastro(self):
        self.assertRodadaFalha(
            [
                _Resposta(pagina_do_omie([item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS")], 1, 2)),
                _Resposta({"pagina": 2, "total_de_paginas": 2}),
            ],
            "página 2",
        )

    def test_menos_registros_que_o_anunciado(self):
        pagina = pagina_do_omie([item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS")])
        pagina["total_de_registros"] = 2
        self.assertRodadaFalha([_Resposta(pagina)], "anunciou 2")

    def test_mais_paginas_que_o_teto_falha_em_vez_de_cortar(self):
        pagina = pagina_do_omie([item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS")], 1, 21)
        self.assertRodadaFalha([_Resposta(pagina)], "21 páginas")

    def test_grupo_so_com_totalizadora_diz_o_motivo(self):
        itens = [item_do_omie("1.10.90", "APN Subgrupo", totalizadora="S"), item_do_omie("1.06.95", "EAP")]
        self.assertRodadaFalha([_Resposta(pagina_do_omie(itens))], "totalizadoras")

    @override_settings(CATEGORIAS_FINANCEIRAS_PREFIXOS="9.99")
    def test_prefixo_que_nao_acha_nada_diz_o_motivo(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resposta(pagina_do_omie(PLANO))):
            with self.assertRaises(FonteIndisponivel) as contexto:
                listar_categorias()
        self.assertIn("9.99", str(contexto.exception))

    def test_faultstring_vira_indisponivel_sem_vazar_a_chave(self):
        corpo = json.dumps({"faultstring": "ERROR: Chave de acesso inválida", "faultcode": "SOAP-ENV:Client-102"}).encode()
        erro = urllib.error.HTTPError(URL, 403, "Forbidden", {}, io.BytesIO(corpo))
        with mock.patch("urllib.request.urlopen", side_effect=erro):
            with self.assertRaises(FonteIndisponivel) as contexto:
                listar_categorias()
        mensagem = str(contexto.exception)
        self.assertIn("403", mensagem)
        self.assertIn("Chave de acesso inválida", mensagem)
        self.assertNotIn(CHAVE, mensagem)
        self.assertNotIn(SEGREDO, mensagem)

    @override_settings(OMIE_APP_KEY="", OMIE_APP_SECRET="")
    def test_sem_credencial_nem_chama_o_omie(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(FonteIndisponivel):
                listar_categorias()
        urlopen.assert_not_called()

    @override_settings(CATEGORIAS_FINANCEIRAS_PREFIXOS=" , ")
    def test_prefixo_vazio_nem_chama_o_omie(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(FonteIndisponivel):
                listar_categorias()
        urlopen.assert_not_called()


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

    @override_settings(OMIE_APP_KEY="", OMIE_APP_SECRET="")
    def test_sem_chave_do_omie_a_rodada_falha_e_diz_por_que(self):
        """O container loga e segue; o banco fica como estava."""
        erro = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("sincronizar_categorias_financeiras", stdout=io.StringIO(), stderr=erro)

        self.assertIn("OMIE_APP_KEY", erro.getvalue())
        self.assertFalse(CategoriaFinanceira.objects.exists())

    @override_settings(OMIE_APP_KEY=CHAVE, OMIE_APP_SECRET=SEGREDO, CATEGORIAS_FINANCEIRAS_PREFIXOS="1.10")
    def test_do_omie_ao_banco(self):
        """Ponta a ponta, sem mock no meio: resposta do Omie, comando, rota pública."""
        saida = io.StringIO()
        with mock.patch("urllib.request.urlopen", return_value=_Resposta(pagina_do_omie(PLANO))):
            call_command("sincronizar_categorias_financeiras", stdout=saida)

        self.assertIn("2 ativas", saida.getvalue())
        corpo = APIClient().get(ROTA).json()
        # Sem depender da ordem: o SQLite do CI e o Postgres de produção ordenam
        # a pontuação diferente.
        self.assertCountEqual(
            corpo["categorias"],
            [
                {"codigo": "1.10.94", "descricao": "APN (Indefinido)"},
                {"codigo": "1.10.93", "descricao": "APN 107 FLORIANÓPOLIS"},
            ],
        )
        self.assertIsNotNone(corpo["sincronizado_em"])

    @override_settings(OMIE_APP_KEY=CHAVE, OMIE_APP_SECRET=SEGREDO, CATEGORIAS_FINANCEIRAS_PREFIXOS="1.10")
    def test_resposta_incompleta_do_omie_nao_desativa_ninguem(self):
        """Ponta a ponta: a 2ª página vem com faultstring, e o banco fica como estava."""
        sincronizar([cat("1.10.93", "APN 107 FLORIANÓPOLIS"), cat("1.10.99", "APN 108 FORTALEZA")])
        respostas = [
            _Resposta(pagina_do_omie([item_do_omie("1.10.93", "APN 107 FLORIANÓPOLIS")], 1, 2)),
            _Resposta({"faultstring": "ERROR: Consumo redundante"}),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=respostas):
            with self.assertRaises(CommandError):
                call_command(
                    "sincronizar_categorias_financeiras", stdout=io.StringIO(), stderr=io.StringIO()
                )
        self.assertEqual(CategoriaFinanceira.objects.filter(ativo=True).count(), 2)

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
