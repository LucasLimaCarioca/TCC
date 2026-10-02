"""Correções solicitadas após a Fase 4; dados e bancos inteiramente fictícios."""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.contexto_conversa import ContextoConversa
from app.models.materia_prima import MateriaPrima
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.operacao_estoque import OperacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.services import estoque_service as estoque
from app.services.estoque_client import EstoqueClient


def assert_sem_operacoes():
    assert Venda.query.count() == 0
    assert MovimentacaoEstoque.query.count() == 0
    assert OperacaoEstoque.query.count() == 0


def test_pedido_vazio_rejeitado_no_dominio_e_no_cliente(app):
    with app.app_context():
        with pytest.raises(estoque.EstoqueError) as error:
            estoque.agrupar_itens([])
        assert error.value.code == "INVALID_INPUT"
        with pytest.raises(estoque.EstoqueError):
            estoque.registrar_venda([], operacao_id=str(uuid4()))
        result = EstoqueClient().registrar_pedido([])
        assert result == (False, "Informe ao menos um produto no pedido.", [])
        assert_sem_operacoes()


def test_http_contexto_com_pedido_vazio_nao_vende(client, app, catalogo):
    key = str(uuid4())
    with app.app_context():
        db.session.add(ContextoConversa(cliente_nome="Cliente Simulado", etapa="aguardando_confirmacao",
            produto_id=catalogo["caixa de 10L - chocolate"], quantidade=1, itens_json="[]", operacao_id=key))
        db.session.commit()
    response = client.post("/api/atendimento", json={"mensagem": "sim"})
    assert response.status_code == 200
    assert response.get_json()["resposta_agente"].startswith("Informe ao menos um produto no pedido.")
    with app.app_context():
        assert_sem_operacoes()
        assert ContextoConversa.query.one().operacao_id == key
    assert client.post("/api/vendas", json={}).status_code == 400
    with app.app_context():
        assert_sem_operacoes()


@pytest.mark.parametrize("acao", ["repor", "substituir", "cancelar"])
def test_estoque_insuficiente_preserva_pedido_e_permite_proxima_acao(app, catalogo, conversar, acao):
    product = catalogo["caixa de 10L - chocolate"]
    conversar("quero 2 caixas de 10L chocolate")
    with app.app_context():
        original_key = ContextoConversa.query.one().operacao_id
        estoque.movimentar("produto", product, "ajuste", 1, "Contagem fictícia")
    failed = conversar("sim")
    assert failed.startswith("No momento temos apenas 1 unidades")
    assert "enviar outro pedido" in failed and "cancelar" in failed
    with app.app_context():
        assert ContextoConversa.query.one().operacao_id == original_key
        assert Venda.query.count() == 0
        assert db.session.get(Produto, product).quantidade_disponivel == 1
    if acao == "repor":
        with app.app_context():
            estoque.movimentar("produto", product, "entrada", 1, "Reposição fictícia")
        assert "Venda registrada!" in conversar("sim")
        with app.app_context():
            assert db.session.get(OperacaoEstoque, original_key).tipo == "venda"
            assert Venda.query.one().quantidade == 2
    elif acao == "substituir":
        conversar("quero 1 caixa de 10L chocolate")
        with app.app_context():
            assert ContextoConversa.query.one().operacao_id != original_key
            assert db.session.get(OperacaoEstoque, original_key) is None
        assert "Venda registrada!" in conversar("sim")
        with app.app_context():
            assert Venda.query.one().quantidade == 1
    else:
        assert conversar("não").startswith("Pedido cancelado.")
        with app.app_context():
            assert Venda.query.count() == 0
    with app.app_context():
        assert ContextoConversa.query.count() == 0


def test_gets_apenas_leem_e_sincronizacao_e_explicita(client, app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    recovered = catalogo["caixa de 5L - morango"]
    with app.app_context():
        db.session.get(Produto, product).quantidade_disponivel = 0
        db.session.add(AlertaEstoque(tipo_item="produto", produto_id=recovered, tipo_alerta="estoque_minimo",
            mensagem="Alerta legado fictício", nivel="aviso", ativo=True, criado_em=datetime(2025, 1, 1)))
        db.session.commit()
        engine = db.engine

    def somente_leitura(connection, cursor, statement, parameters, context, executemany):
        assert statement.lstrip().split()[0].upper() == "SELECT", "GET tentou executar escrita ou reservar a transação"

    event.listen(engine, "before_cursor_execute", somente_leitura)
    try:
        for _ in range(2):
            for route in ["/estoque", "/api/estoque", "/api/estoque/alertas", "/api/estoque/materias-primas", "/api/estoque/movimentacoes", "/api/produtos"]:
                assert client.get(route).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", somente_leitura)
    with app.app_context():
        alert = AlertaEstoque.query.one()
        assert alert.ativo and alert.produto_id == recovered
        assert alert.mensagem == "Alerta legado fictício" and alert.criado_em == datetime(2025, 1, 1)
        assert alert.resolvido_em is None
        assert_sem_operacoes()
    assert client.post("/api/estoque/alertas/sincronizar").status_code == 200
    with app.app_context():
        alerts = AlertaEstoque.query.order_by(AlertaEstoque.id).all()
        assert len(alerts) == 2
        assert alerts[0].resolvido_em and not alerts[0].ativo
        assert alerts[1].produto_id == product and alerts[1].ativo
        dates = [(a.id, a.criado_em, a.resolvido_em) for a in alerts]
    assert client.post("/api/estoque/alertas/sincronizar").status_code == 200
    with app.app_context():
        assert [(a.id, a.criado_em, a.resolvido_em) for a in AlertaEstoque.query.order_by(AlertaEstoque.id)] == dates


@pytest.mark.parametrize("endpoint", ["vendas", "movimentacoes"])
@pytest.mark.parametrize("iguais", [False, True])
def test_duas_chaves_sao_validadas_antes_de_mutar(client, app, catalogo, endpoint, iguais):
    key = str(uuid4())
    product = catalogo["caixa de 10L - chocolate"]
    if endpoint == "vendas":
        route = "/api/vendas"
        data = {"produto_id": product, "quantidade": 2}
    else:
        route = "/api/estoque/movimentacoes"
        data = {"tipo_item": "produto", "item_id": product, "tipo_movimentacao": "saida", "quantidade": 2, "motivo": "Saída fictícia"}
    data["operacao_id"] = key if iguais else str(uuid4())
    response = client.post(route, json=data, headers={"Idempotency-Key": key})
    assert response.status_code == (201 if iguais else 400)
    if iguais:
        assert client.post(route, json=data, headers={"Idempotency-Key": key}).get_json() == response.get_json()
    with app.app_context():
        assert db.session.get(Produto, product).quantidade_disponivel == (18 if iguais else 20)
        if iguais:
            assert OperacaoEstoque.query.count() == MovimentacaoEstoque.query.count() == 1
        else:
            assert response.get_json()["code"] == "INVALID_OPERATION"
            assert_sem_operacoes()


def test_cliente_normalizado_no_hash_e_na_persistencia(app, catalogo):
    with app.app_context():
        key = str(uuid4())
        items = [{"produto_id": catalogo["caixa de 10L - chocolate"], "quantidade": 2}]
        first = estoque.registrar_venda(items, "  Cliente Fictício  ", key)
        sucesso, _, vendas = EstoqueClient().registrar_pedido(items, "Cliente Fictício", key)
        assert sucesso and vendas[0].id == first["vendas"][0]
        assert Venda.query.one().cliente_nome == "Cliente Fictício"
        assert OperacaoEstoque.query.count() == 1


def test_http_cliente_normalizado_reutiliza_mesma_venda(client, app, catalogo):
    key = str(uuid4())
    data = {"produto_id": catalogo["caixa de 10L - chocolate"], "quantidade": 2, "cliente_nome": " Cliente Fictício "}
    first = client.post("/api/vendas", json=data, headers={"Idempotency-Key": key})
    assert first.status_code == 201 and first.get_json()["venda"]["cliente_nome"] == "Cliente Fictício"
    retry = client.post("/api/vendas", json={**data, "cliente_nome": "Cliente Fictício"}, headers={"Idempotency-Key": key})
    assert retry.get_json() == first.get_json()
    with app.app_context():
        assert Venda.query.count() == 1


def test_codigo_materia_prima_normalizado_e_legado_equivalente_rejeitado(app):
    with app.app_context():
        key = estoque.cadastrar_materia_prima(" mp-001 ", " Insumo fictício ", " kg ")["id"]
        material = db.session.get(MateriaPrima, key)
        assert (material.codigo, material.nome, material.unidade_medida) == ("MP-001", "Insumo fictício", "kg")
        with pytest.raises(estoque.EstoqueError) as error:
            estoque.cadastrar_materia_prima("mp-001", "Outro fictício", "kg")
        assert error.value.code == "DUPLICATE_MATERIAL"
        db.session.add(MateriaPrima(codigo="mp-legado", nome="Legado fictício", unidade_medida="kg"))
        db.session.commit()
        with pytest.raises(estoque.EstoqueError):
            estoque.cadastrar_materia_prima(" MP-LEGADO ", "Outro fictício", "kg")
        assert MateriaPrima.query.count() == 2


def test_decimais_equivalentes_reutilizam_operacao(app):
    with app.app_context():
        material = estoque.cadastrar_materia_prima("MP-001", "Insumo fictício", "kg")["id"]
        key = str(uuid4())
        first = estoque.movimentar("materia_prima", material, "entrada", "1.2500", "Reposição fictícia", key)
        assert estoque.movimentar("materia_prima", material, "entrada", "1.25", "Reposição fictícia", key) == first
        assert MovimentacaoEstoque.query.count() == OperacaoEstoque.query.count() == 1


def test_chat_normaliza_nome_antes_de_contexto_e_historico(client, app):
    client.post("/api/atendimento", json={"mensagem": "quero 1 caixa de 10L chocolate", "cliente_nome": " Cliente Fictício "})
    response = client.post("/api/atendimento", json={"mensagem": "sim", "cliente_nome": "Cliente Fictício"})
    assert "Venda registrada!" in response.get_json()["resposta_agente"]
    with app.app_context():
        assert ContextoConversa.query.count() == 0
        assert Venda.query.one().cliente_nome == "Cliente Fictício"
