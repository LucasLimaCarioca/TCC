"""Cadastro com estoque inicial: somente materiais e valores fictícios."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.materia_prima import MateriaPrima
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.operacao_estoque import OperacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.services import estoque_service as estoque
from app.services.estoque_service import EstoqueError
from test_agent_runtime import runtime


def _counts():
    return {
        "materiais": MateriaPrima.query.count(),
        "movimentacoes": MovimentacaoEstoque.query.count(),
        "alertas": AlertaEstoque.query.count(),
        "operacoes": OperacaoEstoque.query.count(),
    }


@pytest.mark.parametrize("unit", ["kg", "L", "mL"])
def test_cadastro_com_saldo_inicial_decimal_cria_entrada_auditavel_na_unidade(app, unit):
    with app.app_context():
        result = estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", unit,
                                                "2", quantidade_inicial="1.123456789")
        assert set(result) == {"id"}
        material = db.session.get(MateriaPrima, result["id"])
        assert material.unidade_medida == unit
        assert material.quantidade_disponivel == Decimal("1.123456789")
        movement = MovimentacaoEstoque.query.one()
        assert movement.materia_prima_id == material.id
        assert movement.tipo_item == "materia_prima" and movement.produto_id is None
        assert movement.tipo_movimentacao == "entrada" and movement.venda_id is None
        assert movement.quantidade == movement.saldo_posterior == Decimal("1.123456789")
        assert movement.saldo_anterior == 0
        assert movement.motivo == "Cadastro inicial de matéria-prima."
        alert = AlertaEstoque.query.one()
        assert alert.ativo and alert.materia_prima_id == material.id
        assert alert.tipo_alerta == "estoque_minimo"


@pytest.mark.parametrize("kwargs", [{}, {"quantidade_inicial": "0"}, {"quantidade_inicial": 0}])
def test_saldo_inicial_omitido_ou_zero_preserva_comportamento_anterior(app, kwargs):
    with app.app_context():
        key = estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg", **kwargs)["id"]
        assert db.session.get(MateriaPrima, key).quantidade_disponivel == 0
        assert MovimentacaoEstoque.query.count() == 0
        assert AlertaEstoque.query.filter_by(materia_prima_id=key, ativo=True).count() == 1


@pytest.mark.parametrize("initial,minimum,active", [
    ("0.000000001", "0", False),
    ("1.249999999", "1.25", True),
    ("1.25", "1.25", True),
    ("1.250000001", "1.25", False),
])
def test_alerta_do_cadastro_considera_saldo_inicial_e_limite_inclusivo(app, initial, minimum, active):
    with app.app_context():
        result = estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg", minimum,
                                                quantidade_inicial=initial)
        material = db.session.get(MateriaPrima, result["id"])
        assert material.quantidade_disponivel == Decimal(initial)
        assert bool(AlertaEstoque.query.filter_by(materia_prima_id=material.id, ativo=True).count()) is active


@pytest.mark.parametrize("initial", [
    "-1", "NaN", "Infinity", "0.0000000001", "10000000000000000", "", None, True, 0.5, [], {},
])
def test_saldo_inicial_invalido_nao_persiste_parcialmente(app, initial):
    with app.app_context():
        before = _counts()
        with pytest.raises(EstoqueError) as caught:
            estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg", quantidade_inicial=initial)
        assert caught.value.code == "INVALID_QUANTITY"
        assert _counts() == before


def test_perda_de_precisao_no_sqlite_cancela_cadastro_inteiro(app):
    with app.app_context():
        before = _counts()
        with pytest.raises(EstoqueError) as caught:
            estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg",
                                            quantidade_inicial="123456789.123456789")
        assert caught.value.code == "INVALID_QUANTITY"
        assert "precisão" in str(caught.value)
        assert _counts() == before


def test_falha_ao_gravar_movimentacao_reverte_material_saldo_e_alerta(app):
    def fail(mapper, connection, target):
        raise RuntimeError("Falha fictícia na movimentação.")
    with app.app_context():
        before = _counts()
        event.listen(MovimentacaoEstoque, "before_insert", fail)
        try:
            with pytest.raises(RuntimeError):
                estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg", "5",
                                                quantidade_inicial="2.5")
        finally:
            event.remove(MovimentacaoEstoque, "before_insert", fail)
        assert _counts() == before


def test_falha_ao_gravar_alerta_reverte_material_e_entrada(app):
    def fail(mapper, connection, target):
        raise RuntimeError("Falha fictícia no alerta.")
    with app.app_context():
        before = _counts()
        event.listen(AlertaEstoque, "before_insert", fail)
        try:
            with pytest.raises(RuntimeError):
                estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg", "5",
                                                quantidade_inicial="2.5")
        finally:
            event.remove(AlertaEstoque, "before_insert", fail)
        assert _counts() == before


def test_falha_no_commit_reverte_todo_cadastro(app, monkeypatch):
    with app.app_context():
        before = _counts()
        def fail(session):
            raise RuntimeError("Falha fictícia no commit.")
        monkeypatch.setattr(Session, "commit", fail)
        with pytest.raises(RuntimeError):
            estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg", "5",
                                            quantidade_inicial="2.5")
        assert _counts() == before


def test_reenvio_do_mesmo_codigo_nao_duplica_saldo_ou_movimentacao(app):
    with app.app_context():
        result = estoque.cadastrar_materia_prima(" mp-ficticia ", "Insumo fictício", "kg",
                                                quantidade_inicial="2.5")
        before = _counts()
        with pytest.raises(EstoqueError) as caught:
            estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg",
                                            quantidade_inicial="2.5")
        assert caught.value.code == "DUPLICATE_MATERIAL"
        assert _counts() == before
        assert db.session.get(MateriaPrima, result["id"]).quantidade_disponivel == Decimal("2.5")


def test_submissoes_concorrentes_do_codigo_creditam_saldo_uma_vez(app):
    def submit():
        with app.app_context():
            try:
                return ("created", estoque.cadastrar_materia_prima(
                    "MP-FICTICIA", "Insumo fictício", "kg", quantidade_inicial="2.5"))
            except EstoqueError as error:
                return ("rejected", error.code)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(result[0] for result in results) == ["created", "rejected"]
    assert next(result[1] for result in results if result[0] == "rejected") == "DUPLICATE_MATERIAL"
    with app.app_context():
        assert MateriaPrima.query.one().quantidade_disponivel == Decimal("2.5")
        assert MovimentacaoEstoque.query.count() == 1


def test_http_cadastro_com_saldo_inicial_e_reenvio_tratado(client, app):
    payload = {"codigo": "MP-FICTICIA", "nome": "Insumo fictício", "unidade_medida": "kg",
               "estoque_minimo": "1.5", "quantidade_inicial": "1.123456789"}
    response = client.post("/api/estoque/materias-primas", json=payload)
    assert response.status_code == 201
    key = response.get_json()["id"]
    repeated = client.post("/api/estoque/materias-primas", json=payload)
    assert repeated.status_code == 409 and repeated.get_json()["code"] == "DUPLICATE_MATERIAL"
    with app.app_context():
        assert db.session.get(MateriaPrima, key).quantidade_disponivel == Decimal(payload["quantidade_inicial"])
        assert MovimentacaoEstoque.query.count() == 1


@pytest.mark.parametrize("initial", ["-1", "0.0000000001", True, 0.5])
def test_http_saldo_inicial_invalido_retorna_400_sem_material(client, app, initial):
    response = client.post("/api/estoque/materias-primas", json={
        "codigo": "MP-FICTICIA", "nome": "Insumo fictício", "unidade_medida": "kg",
        "quantidade_inicial": initial})
    assert response.status_code == 400 and response.get_json()["code"] == "INVALID_QUANTITY"
    with app.app_context():
        assert MateriaPrima.query.count() == MovimentacaoEstoque.query.count() == 0


def test_http_encaminha_cadastro_inicial_pelo_runtime_real(runtime, app):
    response = app.test_client().post("/api/estoque/materias-primas", json={
        "codigo": "MP-FICTICIA", "nome": "Insumo fictício", "unidade_medida": "L",
        "quantidade_inicial": "0.123456789"})
    assert response.status_code == 201
    with app.app_context():
        assert MateriaPrima.query.one().quantidade_disponivel == Decimal("0.123456789")
        assert MovimentacaoEstoque.query.one().quantidade == Decimal("0.123456789")


def test_venda_nao_abate_materia_prima_com_saldo_inicial(app, catalogo):
    with app.app_context():
        key = estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg",
                                             quantidade_inicial="2.5")["id"]
        before = _counts()
        estoque.registrar_venda([{"produto_id": catalogo["caixa de 10L - chocolate"], "quantidade": 1}])
        assert db.session.get(MateriaPrima, key).quantidade_disponivel == Decimal("2.5")
        assert MovimentacaoEstoque.query.filter_by(tipo_item="materia_prima").count() == before["movimentacoes"]
        assert Venda.query.count() == 1
