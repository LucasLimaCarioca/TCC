"""Validação HTTP dos valores enviados pelos steppers; somente itens fictícios."""

from decimal import Decimal

import pytest

from app.database import db
from app.models.materia_prima import MateriaPrima
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto


@pytest.mark.parametrize("value", [1.5, "1.5", "1,5", -1, True])
@pytest.mark.parametrize("operation", ["movimentacao", "minimo"])
def test_produto_rejeita_fracao_ou_quantidade_invalida_sem_alterar_estado(client, app, catalogo, value, operation):
    product = catalogo["caixa de 10L - chocolate"]
    payload = {"tipo_item": "produto", "item_id": product}
    if operation == "movimentacao":
        response = client.post("/api/estoque/movimentacoes", json={**payload,
            "tipo_movimentacao": "entrada", "quantidade": value, "motivo": "Controle fictício"})
    else:
        response = client.put("/api/estoque/minimo", json={**payload, "estoque_minimo": value})
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_QUANTITY"
    with app.app_context():
        item = db.session.get(Produto, product)
        assert item.quantidade_disponivel == 20 and item.estoque_minimo == 0
        assert MovimentacaoEstoque.query.count() == 0


def test_materia_prima_preserva_nona_casa_em_movimentacao_e_minimo(client, app):
    created = client.post("/api/estoque/materias-primas", json={"codigo": "CONTROLE-FICTICIO",
        "nome": "Insumo fictício para controles", "unidade_medida": "kg"})
    assert created.status_code == 201
    identifier = created.get_json()["id"]
    item = {"tipo_item": "materia_prima", "item_id": identifier}
    assert client.post("/api/estoque/movimentacoes", json={**item, "tipo_movimentacao": "entrada",
        "quantidade": "1.500000001", "motivo": "Controle fictício"}).status_code == 201
    assert client.put("/api/estoque/minimo", json={**item, "estoque_minimo": "0.123456789"}).status_code == 200
    with app.app_context():
        material = db.session.get(MateriaPrima, identifier)
        movement = MovimentacaoEstoque.query.one()
        assert material.quantidade_disponivel == Decimal("1.500000001")
        assert material.estoque_minimo == Decimal("0.123456789")
        assert movement.quantidade == movement.saldo_posterior == Decimal("1.500000001")


@pytest.mark.parametrize("value", ["1.1234567891", "1,5", "-0.5", 1.5])
@pytest.mark.parametrize("operation", ["movimentacao", "minimo"])
def test_materia_prima_mantem_validacao_http_decimal_existente(client, app, value, operation):
    created = client.post("/api/estoque/materias-primas", json={"codigo": "PRECISAO-FICTICIA",
        "nome": "Insumo fictício para precisão", "unidade_medida": "L"})
    identifier = created.get_json()["id"]
    item = {"tipo_item": "materia_prima", "item_id": identifier}
    if operation == "movimentacao":
        response = client.post("/api/estoque/movimentacoes", json={**item, "tipo_movimentacao": "entrada",
            "quantidade": value, "motivo": "Controle fictício"})
    else:
        response = client.put("/api/estoque/minimo", json={**item, "estoque_minimo": value})
    assert response.status_code == 400
    with app.app_context():
        material = db.session.get(MateriaPrima, identifier)
        assert material.quantidade_disponivel == material.estoque_minimo == 0
        assert MovimentacaoEstoque.query.count() == 0


def test_produto_aceita_unidade_inteira_e_minimo_zero(client, app, catalogo):
    identifier = catalogo["caixa de 10L - chocolate"]
    item = {"tipo_item": "produto", "item_id": identifier}
    assert client.post("/api/estoque/movimentacoes", json={**item, "tipo_movimentacao": "entrada",
        "quantidade": 1, "motivo": "Controle fictício"}).status_code == 201
    assert client.put("/api/estoque/minimo", json={**item, "estoque_minimo": 0}).status_code == 200
    with app.app_context():
        product = db.session.get(Produto, identifier)
        assert product.quantidade_disponivel == 21 and product.estoque_minimo == 0
        assert MovimentacaoEstoque.query.one().quantidade == 1
