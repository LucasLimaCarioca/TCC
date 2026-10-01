"""Persistência e validações públicas do serviço de vendas do TCC I."""

import pytest

from app.database import db
from app.models.produto import Produto
from app.models.venda import Venda
from app.services.venda_service import registrar_venda, registrar_vendas_multiplas


def test_venda_simples_persiste_total_cliente_e_saldo(app, catalogo):
    produto_id = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        sucesso, mensagem, venda = registrar_venda(produto_id, 2, "Cliente Teste")
        assert sucesso is True
        assert mensagem == "Venda registrada com sucesso."
        venda_id = venda.id
        # Uma nova sessão comprova que os dados foram efetivamente gravados.
        db.session.remove()
        venda = db.session.get(Venda, venda_id)
        assert venda.produto_id == produto_id
        assert venda.quantidade == 2
        assert venda.valor_total == 204.0
        assert venda.cliente_nome == "Cliente Teste"
        assert venda.data_venda is not None
        assert db.session.get(Produto, produto_id).quantidade_disponivel == 18


def test_venda_exatamente_igual_ao_saldo_e_cliente_padrao(app, catalogo):
    produto_id = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        sucesso, _, venda = registrar_venda(produto_id, 20)
        assert sucesso is True
        assert venda.cliente_nome == "Cliente Simulado"
        assert venda.valor_total == 2040.0
        assert db.session.get(Produto, produto_id).quantidade_disponivel == 0


@pytest.mark.parametrize(
    "cenario,quantidade,mensagem",
    [
        ("inexistente", 1, "Produto não encontrado."),
        ("inativo", 1, "Produto não encontrado."),
        ("normal", 0, "Informe uma quantidade maior que zero."),
        ("normal", -1, "Informe uma quantidade maior que zero."),
        ("zerado", 1, "No momento não temos caixa de 10L - chocolate em estoque."),
        ("normal", 21, "No momento temos apenas 20 unidades de caixa de 10L - chocolate."),
    ],
)
def test_venda_invalida_nao_persiste_nem_altera_saldo(app, catalogo, cenario, quantidade, mensagem):
    produto_id = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        produto = db.session.get(Produto, produto_id)
        if cenario == "inativo":
            produto.ativo = False
        if cenario == "zerado":
            produto.quantidade_disponivel = 0
        db.session.commit()
        saldo = produto.quantidade_disponivel
        id_venda = max(catalogo.values()) + 1 if cenario == "inexistente" else produto_id
        assert registrar_venda(id_venda, quantidade) == (False, mensagem, None)
        db.session.remove()
        assert Venda.query.count() == 0
        assert db.session.get(Produto, produto_id).quantidade_disponivel == saldo


def test_venda_multipla_persiste_todos_os_itens(app, catalogo):
    chocolate = catalogo["caixa de 10L - chocolate"]
    morango = catalogo["caixa de 5L - morango"]
    with app.app_context():
        sucesso, mensagem, vendas = registrar_vendas_multiplas([
            {"produto_id": chocolate, "quantidade": 2},
            {"produto_id": morango, "quantidade": 3},
        ], cliente_nome="Cliente Simulado 2")
        assert sucesso is True
        assert mensagem == "Venda registrada com sucesso."
        assert len(vendas) == 2
        db.session.remove()
        vendas = Venda.query.order_by(Venda.id).all()
        assert [(v.produto_id, v.quantidade, v.valor_total) for v in vendas] == [
            (chocolate, 2, 204.0), (morango, 3, 198.0),
        ]
        assert all(v.cliente_nome == "Cliente Simulado 2" for v in vendas)
        assert db.session.get(Produto, chocolate).quantidade_disponivel == 18
        assert db.session.get(Produto, morango).quantidade_disponivel == 17


@pytest.mark.parametrize("falha", ["inexistente", "inativo", "sem_estoque", "quantidade_zero"])
def test_falha_no_segundo_item_nao_vende_primeiro(app, catalogo, falha):
    chocolate = catalogo["caixa de 10L - chocolate"]
    morango = catalogo["caixa de 5L - morango"]
    with app.app_context():
        if falha == "inativo":
            db.session.get(Produto, morango).ativo = False
            db.session.commit()
        segundo_id = max(catalogo.values()) + 1 if falha == "inexistente" else morango
        quantidade = {"sem_estoque": 21, "quantidade_zero": 0}.get(falha, 1)
        sucesso, mensagem, vendas = registrar_vendas_multiplas([
            {"produto_id": chocolate, "quantidade": 2},
            {"produto_id": segundo_id, "quantidade": quantidade},
        ])
        assert sucesso is False
        assert mensagem
        assert vendas == []
        db.session.remove()
        assert Venda.query.count() == 0
        assert db.session.get(Produto, chocolate).quantidade_disponivel == 20
        assert db.session.get(Produto, morango).quantidade_disponivel == 20
