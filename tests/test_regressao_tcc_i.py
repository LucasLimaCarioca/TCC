"""Os oito casos de regressão exigidos pelo plano, via API e banco reais."""

import json

from app.database import db
from app.models.contexto_conversa import ContextoConversa
from app.models.historico_conversa import HistoricoConversa
from app.models.produto import Produto
from app.models.venda import Venda


def assert_sem_pedido_ou_venda(app):
    with app.app_context():
        assert Venda.query.count() == 0
        assert ContextoConversa.query.count() == 0


def test_01_consulta_produtos(app, conversar):
    resposta = conversar("quais produtos tem?")
    assert resposta == (
        "Produtos disponíveis por categoria:\n"
        "\ncaixa de 10L:\n- baunilha\n- chocolate\n- morango\n"
        "\ncaixa de 5L:\n- baunilha\n- chocolate\n- morango\n"
        "\ncaixa de picole:\n- caixa A\n- caixa B\n- caixa C\n"
        "\ncaixa de sundae:\n- chocolate\n- morango\n"
    )
    assert_sem_pedido_ou_venda(app)
    with app.app_context():
        registro = HistoricoConversa.query.one()
        assert registro.cliente_nome == "Cliente Simulado"
        assert registro.mensagem_usuario == "quais produtos tem?"
        assert registro.resposta_agente == resposta


def test_02_disponibilidade_especifica(app, conversar):
    assert conversar("Tem caixa de 10L de chocolate?") == (
        "Sim, temos 20 unidades de caixa de 10L - chocolate em estoque."
    )
    assert_sem_pedido_ou_venda(app)


def test_03_pedido_multiplo_com_confirmacao(app, catalogo, conversar):
    chocolate = catalogo["caixa de 10L - chocolate"]
    morango = catalogo["caixa de 5L - morango"]
    assert conversar("quero 2 caixas de 10L chocolate e 1 caixa de 5L morango") == (
        "Encontrei este pedido:\n"
        "- caixa de 10L - chocolate: 2 unidade(s) (R$ 204.00)\n"
        "- caixa de 5L - morango: 1 unidade(s) (R$ 66.00)\n"
        "Total: R$ 270.00\nConfirma a compra? Responda sim ou não."
    )
    with app.app_context():
        contexto = ContextoConversa.query.one()
        assert contexto.etapa == "aguardando_confirmacao"
        assert json.loads(contexto.itens_json) == [
            {"produto_id": chocolate, "quantidade": 2},
            {"produto_id": morango, "quantidade": 1},
        ]
        assert Venda.query.count() == 0
        assert all(p.quantidade_disponivel == 20 for p in Produto.query.all())

    assert conversar("sim") == (
        "Venda registrada!\n"
        "- caixa de 10L - chocolate: 2 unidade(s) (R$ 204.00)\n"
        "- caixa de 5L - morango: 1 unidade(s) (R$ 66.00)\n"
        "Total do pedido: R$ 270.00"
    )
    with app.app_context():
        assert ContextoConversa.query.count() == 0
        vendas = Venda.query.order_by(Venda.id).all()
        assert [(v.produto_id, v.quantidade, v.valor_total) for v in vendas] == [
            (chocolate, 2, 204.0), (morango, 1, 66.0),
        ]
        assert all(v.cliente_nome == "Cliente Simulado" for v in vendas)
        assert db.session.get(Produto, chocolate).quantidade_disponivel == 18
        assert db.session.get(Produto, morango).quantidade_disponivel == 19
        assert HistoricoConversa.query.count() == 2


def test_04_categoria_compartilhada_varios_sabores(app, catalogo, conversar):
    resposta = conversar("quero 1 caixa de 10L de chocolate, morango e baunilha")
    assert resposta.startswith("Encontrei este pedido:\n")
    for sabor, preco in [("chocolate", 102), ("morango", 101), ("baunilha", 100)]:
        assert f"- caixa de 10L - {sabor}: 1 unidade(s) (R$ {preco:.2f})\n" in resposta
    assert "Total: R$ 303.00\n" in resposta
    assert resposta.endswith("Confirma a compra? Responda sim ou não.")
    with app.app_context():
        itens = json.loads(ContextoConversa.query.one().itens_json)
        assert {i["produto_id"]: i["quantidade"] for i in itens} == {
            catalogo[f"caixa de 10L - {sabor}"]: 1
            for sabor in ["chocolate", "morango", "baunilha"]
        }
        assert Venda.query.count() == 0
    assert "Total do pedido: R$ 303.00" in conversar("sim")
    with app.app_context():
        assert Venda.query.count() == 3
        assert ContextoConversa.query.count() == 0
        for produto in Produto.query.all():
            assert produto.quantidade_disponivel == (19 if produto.categoria == "caixa de 10L" else 20)


def test_05_produto_sem_estoque(app, catalogo, conversar):
    with app.app_context():
        db.session.get(Produto, catalogo["caixa de 10L - chocolate"]).quantidade_disponivel = 0
        db.session.commit()
    assert conversar("quero 1 caixa de 10L chocolate") == (
        "No momento não temos caixa de 10L - chocolate em estoque.\n"
        "Você pode escolher outro sabor ou categoria."
    )
    assert_sem_pedido_ou_venda(app)


def test_06_quantidade_superior_estoque(app, conversar):
    assert conversar("quero 21 caixas de 10L chocolate") == (
        "No momento temos apenas 20 unidades de caixa de 10L - chocolate.\n"
        "Você pode pedir uma quantidade menor ou escolher outro sabor."
    )
    assert_sem_pedido_ou_venda(app)
    with app.app_context():
        assert all(p.quantidade_disponivel == 20 for p in Produto.query.all())


def test_07_preco_produto_especifico(app, conversar):
    assert conversar("qual o preço da caixa de 10L chocolate?") == (
        "O valor de caixa de 10L - chocolate é R$ 102.00."
    )
    assert_sem_pedido_ou_venda(app)


def test_08_preco_multiplos_produtos(app, conversar):
    assert conversar("preço da caixa de 10L chocolate e caixa de 5L morango") == (
        "Valores dos produtos solicitados:\n"
        "- caixa de 10L - chocolate: R$ 102.00\n"
        "- caixa de 5L - morango: R$ 66.00\n"
    )
    assert_sem_pedido_ou_venda(app)
