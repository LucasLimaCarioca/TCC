"""Contratos HTTP, tela e fluxo de controle de estoque com catálogo fictício."""

from uuid import uuid4

import pytest

from app.database import db
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda


def test_controle_estoque_com_materias_primas_e_historico(client, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    assert client.put("/api/estoque/minimo", json={"tipo_item": "produto", "item_id": product, "estoque_minimo": 20}).status_code == 200
    assert len(client.get("/api/estoque/alertas").get_json()) == 1
    key = str(uuid4())
    data = {"tipo_item": "produto", "item_id": product, "tipo_movimentacao": "entrada", "quantidade": 3, "motivo": "Reposição fictícia"}
    first = client.post("/api/estoque/movimentacoes", json=data, headers={"Idempotency-Key": key})
    assert first.status_code == 201
    repeat = client.post("/api/estoque/movimentacoes", json=data, headers={"Idempotency-Key": key})
    assert repeat.get_json() == first.get_json()
    assert client.get("/api/estoque/alertas").get_json()[0]["ativo"] is False
    materials = client.post("/api/estoque/materias-primas", json={"codigo": "MP-FICTICIA", "nome": "Insumo fictício", "unidade_medida": "kg"})
    assert materials.status_code == 201
    material = materials.get_json()["id"]
    assert client.post("/api/estoque/movimentacoes", json={"tipo_item": "materia_prima", "item_id": material, "tipo_movimentacao": "entrada", "quantidade": "1.25", "motivo": "Reposição fictícia"}).status_code == 201
    assert len(client.get("/api/estoque/movimentacoes").get_json()) == 2
    assert client.get("/api/estoque/materias-primas").get_json()[0]["quantidade_disponivel"] == "1.250000000"
    page = client.get("/produtos")
    assert page.status_code == 200
    for text in ["Matérias-primas", "Ajuste do saldo final", "Histórico de movimentações", "Reposição fictícia", "Insumo fictício"]:
        assert text in page.get_data(as_text=True)
    assert client.get("/static/js/estoque.js").status_code == 200


def test_api_venda_chave_idempotente(client, app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    key = str(uuid4())
    data = {"produto_id": product, "quantidade": 2}
    first = client.post("/api/vendas", json=data, headers={"Idempotency-Key": key})
    assert first.status_code == 201
    repeat = client.post("/api/vendas", json=data, headers={"Idempotency-Key": key})
    assert repeat.get_json() == first.get_json()
    assert client.post("/api/vendas", json={**data, "quantidade": 3}, headers={"Idempotency-Key": key}).status_code == 400
    with app.app_context():
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 1
        assert db.session.get(Produto, product).quantidade_disponivel == 18


@pytest.mark.parametrize("endpoint,data", [
    ("/api/estoque/movimentacoes", {}),
    ("/api/estoque/movimentacoes", {"tipo_movimentacao": []}),
    ("/api/estoque/movimentacoes", {"tipo_item": "produto", "item_id": 1, "tipo_movimentacao": "venda", "quantidade": 1, "motivo": "Teste"}),
    ("/api/estoque/movimentacoes", {"tipo_item": "produto", "item_id": 1, "tipo_movimentacao": "entrada", "quantidade": 1, "motivo": ""}),
    ("/api/estoque/materias-primas", {}),
    ("/api/estoque/materias-primas", []),
    ("/api/vendas", {}),
    ("/api/vendas", {"produto_id": 1, "quantidade": True}),
    ("/api/vendas", {"produto_id": 1, "quantidade": 1.5}),
    ("/api/vendas", {"produto_id": 1, "quantidade": 1, "operacao_id": "invalido"}),
])
def test_requisicoes_invalidas_nao_alteram_saldo(client, app, endpoint, data):
    assert client.post(endpoint, json=data).status_code == 400
    with app.app_context():
        assert all(p.quantidade_disponivel == 20 for p in Produto.query.all())
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 0


def test_materia_prima_duplicada_e_movimento_chave_conflitante(client, catalogo):
    material = {"codigo": "MP-FICTICIA", "nome": "Insumo fictício", "unidade_medida": "kg"}
    assert client.post("/api/estoque/materias-primas", json=material).status_code == 201
    assert client.post("/api/estoque/materias-primas", json=material).status_code == 409
    data = {"tipo_item": "produto", "item_id": catalogo["caixa de 10L - chocolate"], "tipo_movimentacao": "entrada", "quantidade": 1, "motivo": "Teste fictício"}
    key = str(uuid4())
    assert client.post("/api/estoque/movimentacoes", json=data, headers={"Idempotency-Key": key}).status_code == 201
    assert client.post("/api/estoque/movimentacoes", json={**data, "quantidade": 2}, headers={"Idempotency-Key": key}).status_code == 409


def test_risco_sem_runtime_informa_indisponibilidade(client, app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    response = client.get(f"/api/estoque/risco/{product}")
    if "agent_gateway" in app.extensions:
        assert response.status_code == 200
        assert response.get_json()["status"] == "previsao_indisponivel"
        assert response.get_json()["risco"] is None
    else:
        assert response.status_code == 503
