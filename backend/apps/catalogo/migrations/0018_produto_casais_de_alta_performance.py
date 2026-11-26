"""
O Casais de Alta Performance entra na categoria Produtos (TSK-1622, 25/11/2026).

O PRODUTO

`Casais de Alta Performance`, sigla `CAP`, `fluxo="casais"`: formulário próprio,
como o DH - Avaliação (a `0012`), e sem preço de tabela. O valor da venda é um de
dois fixos, decididos pelo dono no mesmo dia — R$ 39.997,00 (preço cheio) ou
R$ 35.000,00 (Elite Empresarial) —, e quem escolhe entre os dois não é a tabela:
na PJ é o Conecta, que diz se o CNPJ é cliente ativo; na PF, o consultor. As
regras moram em `apps.casais` e no comprovante (`apps.vendas.servicos`).

Entra no fim da lista da categoria (a maior `ordem` + 1): o que já está no ar não
muda de lugar na tela do consultor (`tests_contrato`, a ordem relativa).

Como a `0010`, não grava `Vigencia` nem `PublicacaoCatalogo`: vigência é
histórico de VALOR, e este produto não tem valor de tabela a historiar; e
publicação é o registro de quem apertou o botão, e não há pessoa por trás disto.

AS CONFERÊNCIAS

Como a `0003`, a `0010` e a `0012`, ela para com a mensagem em português em vez de
deixar o banco reclamar: se a sigla CAP já estiver em uso (produto ou categoria;
é o SSS do protocolo da venda), se já houver outro produto com o identificador
`casais-alta-performance` em Produtos, ou se a categoria Produtos não existir.
Rodar de novo, ou depois de o produto ter sido criado à mão com o fluxo certo,
não faz nada.

O DESFAZER

Apaga o produto, a não ser que ele já tenha venda emitida (`ComprovanteVenda`):
aí para, pelo motivo da `0010` — protocolo emitido é registro lá fora, e apagar o
produto o deixaria órfão.

DEPLOY: esta migration e o `index.html` andam juntos, pelo motivo da `0010` e da
`0012`. Um front que não conhece o fluxo `casais` mostra o produto na lista sem
preço e não sabe abri-lo; e um front novo sem a migration abre o formulário pelo
catálogo embutido, mas o comprovante recusa a venda ("Produto de formulário não
encontrado"). Migration antes do `up -d`, como manda o README.
"""
from django.db import migrations, models
from django.db.models import Max

CATEGORIA = "produtos"
FLUXO = "casais"

PRODUTO = {
    "slug": "casais-alta-performance",
    "nome": "Casais de Alta Performance",
    "sigla": "CAP",
    "descricao": "Programa para o casal — um contrato, dois participantes",
    "duracao": "por casal",
    "icone": "diversity_1",
    "fluxo": FLUXO,
}


def _sigla_em_uso(apps, sigla, ignorando=None):
    Produto = apps.get_model("catalogo", "Produto")
    Categoria = apps.get_model("catalogo", "Categoria")
    outro = Produto.objects.filter(sigla=sigla)
    if ignorando is not None:
        outro = outro.exclude(pk=ignorando.pk)
    outro = outro.first()
    if outro is not None:
        return f'o produto "{outro.nome}"'
    categoria = Categoria.objects.filter(sigla=sigla).first()
    if categoria is not None:
        return f'a categoria "{categoria.nome}"'
    return None


def aplicar(apps, schema_editor):
    Categoria = apps.get_model("catalogo", "Categoria")
    Produto = apps.get_model("catalogo", "Produto")

    categoria = Categoria.objects.filter(slug=CATEGORIA).first()
    if categoria is None:
        raise RuntimeError(
            'A categoria Produtos (identificador "produtos") não existe neste banco, '
            "e é nela que o Casais de Alta Performance entra. Confira as categorias no "
            "/django-admin/ (a migration 0010 é quem a cria) e rode o migrate de novo."
        )

    existente = Produto.objects.filter(categoria=categoria, slug=PRODUTO["slug"]).first()
    if existente is not None:
        if existente.fluxo == FLUXO:
            # Já criado (à mão, ou por uma rodada anterior desta migration): fica
            # como está. A constraint `produto_de_fluxo_proprio_nao_tem_preco`
            # garante que ele não guarda preço.
            return
        raise RuntimeError(
            f'Já existe o produto "{existente.nome}" com o identificador '
            f'"{PRODUTO["slug"]}" em Produtos, e ele não é o formulário do Casais de '
            "Alta Performance. Confira o produto no /django-admin/ antes de rodar o "
            "migrate de novo."
        )

    conflito = _sigla_em_uso(apps, PRODUTO["sigla"])
    if conflito:
        raise RuntimeError(
            f"A sigla {PRODUTO['sigla']} já é de {conflito}. Ela é o SSS do protocolo "
            "da venda (o número do contrato do Casais de Alta Performance) e não pode "
            "repetir: troque a sigla de lá e rode o migrate de novo."
        )

    ultima = Produto.objects.filter(categoria=categoria).aggregate(m=Max("ordem"))["m"]
    Produto.objects.create(
        categoria=categoria,
        ordem=(ultima + 1) if ultima is not None else 0,
        recorrente=False,
        **PRODUTO,
    )


def desfazer(apps, schema_editor):
    Produto = apps.get_model("catalogo", "Produto")
    ComprovanteVenda = apps.get_model("vendas", "ComprovanteVenda")

    produto = Produto.objects.filter(
        categoria__slug=CATEGORIA, slug=PRODUTO["slug"], fluxo=FLUXO
    ).first()
    if produto is None:
        return

    vendido = ComprovanteVenda.objects.filter(
        valores__categoria_id=CATEGORIA, valores__produto_id=PRODUTO["slug"]
    ).exists()
    if vendido:
        raise RuntimeError(
            "O Casais de Alta Performance tem venda emitida neste banco: apagá-lo "
            "deixaria o protocolo dela sem produto. Resolva à mão antes de descer "
            "esta migration."
        )
    produto.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('catalogo', '0017_dia_fixo_ligado_em_toda_regra'),
        ('vendas', '0004_fluxo_casais'),
    ]

    operations = [
        migrations.AlterField(
            model_name='produto',
            name='fluxo',
            field=models.CharField(blank=True, choices=[('dh', 'Formulário DH (Recrutamento e Seleção)'), ('dh_avaliacao', 'Formulário DH - Avaliação (PROFILER)'), ('casais', 'Formulário Casais de Alta Performance')], default='', help_text='Vazio = produto de tabela: tem preço aqui e o consultor segue o fluxo padrão. Preenchido, o produto NÃO tem preço — o consultor cai num formulário próprio e os valores saem de lá. Produto assim é somente leitura para a tela da diretoria.', max_length=20, verbose_name='fluxo próprio'),
        ),
        migrations.RunPython(aplicar, desfazer),
    ]
