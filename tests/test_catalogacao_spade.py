"""HTTP e autoridade SPADE reais, com códigos e catálogo exclusivamente fictícios."""

from decimal import Decimal
from threading import get_ident
from uuid import uuid4

import pytest

from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.catalogacao_historica import OperacaoCatalogacao, RevisaoAssociacaoHistorica
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.services.catalogacao_service import associacao_revisada
from test_agent_runtime import runtime
from test_catalogacao_historica import inventory, historical_rows, new_product, payload


def observe_stock_commands(runtime, monkeypatch):
    """Observa a porta real, sem substituir sua execução ou o transporte."""
    calls = []
    original = runtime.agents["estoque"].executar

    async def observed(command, **data):
        calls.append((command, get_ident()))
        return await original(command, **data)

    monkeypatch.setattr(runtime.agents["estoque"], "executar", observed)
    return calls


def test_associacao_revisao_e_conflito_passam_pelo_estoque_spade(app, inventory, runtime, monkeypatch):
    main_thread = get_ident()
    calls = observe_stock_commands(runtime, monkeypatch)
    client = app.test_client()
    with app.app_context():
        before = historical_rows()
        request = payload(product=inventory[0])

    response = client.post("/api/catalogacao/operacoes", json=request)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    operation_id = response.json["operacao_id"]
    with app.app_context():
        product = db.session.get(Produto, inventory[0])
        assert product.codigo_externo == "00770"
        assert associacao_revisada(db.session, product)
        assert RevisaoAssociacaoHistorica.query.filter_by(produto_id=product.id).count() == 1
        assert db.session.get(OperacaoCatalogacao, operation_id).acao == "associar"
        assert historical_rows() == before
        review = payload(action="revisar", product=product.id)

    reviewed = client.post("/api/catalogacao/operacoes", json=review)
    assert reviewed.status_code == 200 and reviewed.json["acao"] == "revisar"
    stale = client.post("/api/catalogacao/operacoes", json={**request, "operacao_id": str(uuid4())})
    assert stale.status_code == 409 and stale.json["code"] == "CATALOG_CHANGED"
    assert stale.headers["Cache-Control"] == "no-store"
    with app.app_context():
        assert historical_rows() == before
        assert OperacaoCatalogacao.query.count() == 2
        assert db.session.get(Produto, inventory[0]).quantidade_disponivel == 8
        assert db.session.get(Produto, inventory[1]).codigo_externo is None
    mutations = [thread for command, thread in calls if command == "catalogar_historico"]
    assert len(mutations) == 3
    assert all(thread != main_thread for thread in mutations)
    assert runtime.running and app.extensions["agent_gateway"].runtime is runtime


@pytest.mark.parametrize("explicit_active", [False, True], ids=["inativo-por-padrao", "ativo-explicito"])
def test_cadastro_inicial_por_http_spade_registra_entrada_revisao_auditoria_e_alerta(
        app, inventory, runtime, monkeypatch, explicit_active):
    main_thread = get_ident()
    calls = observe_stock_commands(runtime, monkeypatch)
    client = app.test_client()
    with app.app_context():
        before = historical_rows()
        request = payload(action="criar")
        request["produto"] = new_product(**({"ativo": True} if explicit_active else {}))

    response = client.post("/api/catalogacao/operacoes", json=request)
    assert response.status_code == 200
    product_id = response.json["produto_ids"][0]
    with app.app_context():
        product = db.session.get(Produto, product_id)
        assert product.codigo_externo == "00770" and product.quantidade_disponivel == 3
        assert product.estoque_minimo == 4 and product.ativo is explicit_active
        assert product.demonstracao is False and associacao_revisada(db.session, product)
        movement = MovimentacaoEstoque.query.filter_by(produto_id=product.id).one()
        assert movement.tipo_movimentacao == "entrada" and "Cadastro inicial" in movement.motivo
        assert (movement.quantidade, movement.saldo_anterior, movement.saldo_posterior) == (
            Decimal(3), Decimal(0), Decimal(3))
        assert AlertaEstoque.query.filter_by(produto_id=product.id,
            tipo_alerta="estoque_minimo", ativo=True).count() == int(explicit_active)
        assert RevisaoAssociacaoHistorica.query.filter_by(produto_id=product.id).count() == 1
        assert db.session.get(OperacaoCatalogacao, response.json["operacao_id"]).acao == "criar"
        assert historical_rows() == before

    # Repetição do recibo percorre o mesmo agente sem creditar saldo novamente.
    repeated = client.post("/api/catalogacao/operacoes", json=request)
    assert repeated.status_code == 200 and repeated.json == response.json
    with app.app_context():
        assert db.session.get(Produto, product_id).quantidade_disponivel == 3
        assert MovimentacaoEstoque.query.filter_by(produto_id=product_id).count() == 1
        assert OperacaoCatalogacao.query.count() == 1
        assert historical_rows() == before
    mutations = [thread for command, thread in calls if command == "catalogar_historico"]
    assert len(mutations) == 2 and all(thread != main_thread for thread in mutations)
