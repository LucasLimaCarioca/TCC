"""Persistência e restrições dos modelos da Fase 1, com dados fictícios."""

from datetime import date
from decimal import Decimal
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.app import create_app
from app.database import db
from app.migrations import upgrade_database
from app.models import (
    AlertaEstoque, LogMensagemAgente, MateriaPrima, MovimentacaoEstoque, OperacaoEstoque,
    PrevisaoDemanda, Produto, VendaHistorica,
)


@pytest.fixture
def migrated(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'modelos.db'}"})
    with app.app_context():
        upgrade_database()  # Banco vazio: não precisa de backup.
        product = Produto(nome="Produto Ficticio", preco=10, quantidade_disponivel=20)
        material = MateriaPrima(codigo="MP-001", nome="Insumo Ficticio", unidade_medida="kg")
        db.session.add_all([product, material])
        db.session.commit()
        yield app, product.id, material.id
        db.session.remove()
        db.engine.dispose()


def history(**changes):
    values = dict(codigo_produto="00110", data=date(2025, 1, 2), codigo_grupo="00005",
                  categoria="Grupo Ficticio", quantidade=Decimal("1.25"), numero_pedidos=1,
                  source_hash=hashlib.sha256(b"linha ficticia").hexdigest(),
                  dataset_hash=hashlib.sha256(b"dataset ficticio").hexdigest())
    values.update(changes)
    return VendaHistorica(**values)


def test_produto_legado_defaults_e_codigo_externo_unico(migrated):
    _, product_id, _ = migrated
    product = db.session.get(Produto, product_id)
    assert product.codigo_externo is None
    assert product.estoque_minimo == 0
    assert product.quantidade_disponivel == 20
    product.codigo_externo = "00110"
    db.session.commit()
    db.session.add(Produto(nome="Outro Ficticio", preco=1, codigo_externo="00110"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert db.session.get(Produto, product_id).codigo_externo == "00110"


def test_minimo_produto_negativo_rejeitado_pelo_banco_migrado(migrated):
    _, product_id, _ = migrated
    db.session.get(Produto, product_id).estoque_minimo = -1
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_materia_prima_defaults_decimal_e_codigo_unico(migrated):
    _, _, material_id = migrated
    material = db.session.get(MateriaPrima, material_id)
    assert material.ativo is True
    assert material.quantidade_disponivel == material.estoque_minimo == 0
    material.quantidade_disponivel = Decimal("1.25")
    db.session.commit()
    db.session.remove()
    assert db.session.get(MateriaPrima, material_id).quantidade_disponivel == Decimal("1.25")
    db.session.add(MateriaPrima(codigo="MP-001", nome="Outro Ficticio", unidade_medida="kg"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize("field", ["quantidade_disponivel", "estoque_minimo"])
def test_materia_prima_rejeita_saldos_negativos(migrated, field):
    _, _, material_id = migrated
    setattr(db.session.get(MateriaPrima, material_id), field, -1)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize("model", [MovimentacaoEstoque, AlertaEstoque])
@pytest.mark.parametrize("item", ["produto", "materia_prima"])
def test_item_referenciado_corretamente(migrated, model, item):
    _, product_id, material_id = migrated
    values = dict(tipo_item=item, produto_id=product_id if item == "produto" else None,
                  materia_prima_id=material_id if item == "materia_prima" else None)
    if model is MovimentacaoEstoque:
        values.update(tipo_movimentacao="entrada", quantidade=Decimal("1.25"), saldo_anterior=0,
                      saldo_posterior=Decimal("1.25"), motivo="Teste ficticio")
    else:
        values.update(tipo_alerta="estoque_minimo", mensagem="Teste ficticio", nivel="aviso")
    obj = model(**values)
    db.session.add(obj)
    db.session.commit()
    db.session.refresh(obj)
    assert obj.criado_em is not None
    if item == "produto":
        assert obj.produto.id == product_id and obj.materia_prima is None
    else:
        assert obj.materia_prima.id == material_id and obj.produto is None
    # Persistir registros não executa automaticamente regras de estoque nesta fase.
    assert db.session.get(Produto, product_id).quantidade_disponivel == 20


@pytest.mark.parametrize("model", [MovimentacaoEstoque, AlertaEstoque])
@pytest.mark.parametrize("case", ["ambos", "nenhum", "tipo_incompativel", "fk_inexistente"])
def test_rejeita_referencias_incoerentes(migrated, model, case):
    _, product_id, material_id = migrated
    values = dict(tipo_item="produto", produto_id=product_id)
    if case == "ambos":
        values["materia_prima_id"] = material_id
    elif case == "nenhum":
        values["produto_id"] = None
    elif case == "tipo_incompativel":
        values["tipo_item"] = "materia_prima"
    else:
        values["produto_id"] = 99999
    if model is MovimentacaoEstoque:
        values.update(tipo_movimentacao="entrada", quantidade=1, saldo_anterior=0,
                      saldo_posterior=1, motivo="Teste ficticio")
    else:
        values.update(tipo_alerta="estoque_minimo", mensagem="Teste ficticio", nivel="aviso")
    db.session.add(model(**values))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_historico_diario_nao_tem_campos_pessoais_e_preserva_codigos(migrated):
    row = history()
    db.session.add(row)
    db.session.commit()
    db.session.remove()
    row = VendaHistorica.query.one()
    assert row.codigo_produto == "00110"
    assert row.codigo_grupo == "00005"
    assert row.categoria == "Grupo Ficticio"
    assert row.quantidade == Decimal("1.25")
    assert row.numero_pedidos == 1
    assert row.importado_em is not None
    assert set(VendaHistorica.__table__.columns.keys()) == {
        "id", "codigo_produto", "data", "codigo_grupo", "categoria", "quantidade",
        "numero_pedidos", "source_hash", "dataset_hash", "importado_em",
    }


@pytest.mark.parametrize("case", ["produto_data", "hash"])
def test_historico_impede_duplicacao(migrated, case):
    db.session.add(history())
    db.session.commit()
    changes = {"source_hash": "a" * 64} if case == "produto_data" else {"data": date(2025, 1, 3)}
    db.session.add(history(**changes))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert VendaHistorica.query.count() == 1


@pytest.mark.parametrize("changes", [{"quantidade": -1}, {"numero_pedidos": -1}, {"source_hash": "curto"}])
def test_historico_rejeita_valores_invalidos(migrated, changes):
    db.session.add(history(**changes))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize("granularity", ["diaria", "semanal", "mensal"])
def test_previsao_persiste_periodo_modelo_e_metricas(migrated, granularity):
    _, product_id, _ = migrated
    row = PrevisaoDemanda(produto_id=product_id, granularidade=granularity,
                         periodo_inicio=date(2025, 1, 1), periodo_fim=date(2025, 1, 31),
                         quantidade_prevista=Decimal("2.5"), modelo="baseline_ficticio",
                         versao_modelo="v1", metricas_json={"mae": 1.25})
    db.session.add(row)
    db.session.commit()
    db.session.remove()
    row = PrevisaoDemanda.query.one()
    assert row.produto.id == product_id
    assert row.metricas_json == {"mae": 1.25}
    assert row.quantidade_prevista == Decimal("2.5")
    assert row.gerada_em is not None


@pytest.mark.parametrize("changes", [
    {"granularidade": "invalida"}, {"quantidade_prevista": -1},
    {"periodo_fim": date(2024, 1, 1)}, {"produto_id": 99999},
])
def test_previsao_rejeita_inconsistencias(migrated, changes):
    _, product_id, _ = migrated
    values = dict(produto_id=product_id, granularidade="diaria", periodo_inicio=date(2025, 1, 1),
                  periodo_fim=date(2025, 1, 1), quantidade_prevista=1, modelo="ficticio", versao_modelo="v1")
    values.update(changes)
    db.session.add(PrevisaoDemanda(**values))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_log_persiste_payload_json_e_correlacao(migrated):
    db.session.add(LogMensagemAgente(sender="estoque@localhost", receiver="previsao@localhost",
                                    performative="request", ontology="previsao.consulta", thread="teste-001",
                                    payload={"produto_id": 1, "horizonte_dias": 7}, status="enviada"))
    db.session.commit()
    db.session.remove()
    row = LogMensagemAgente.query.one()
    assert row.payload == {"produto_id": 1, "horizonte_dias": 7}
    assert row.thread == "teste-001"
    assert row.timestamp is not None


@pytest.mark.parametrize("tipo", ["venda", "movimentacao"])
def test_operacao_estoque_persiste_tipos_suportados(migrated, tipo):
    receipt = OperacaoEstoque(id=str(uuid4()), tipo=tipo, request_hash="a" * 64,
                             resultado={"ficticio": True})
    db.session.add(receipt)
    db.session.commit()
    db.session.remove()
    assert OperacaoEstoque.query.one().resultado == {"ficticio": True}


@pytest.mark.parametrize("changes", [{"tipo": "ajuste"}, {"tipo": ""},
    {"request_hash": "a" * 63}, {"request_hash": "a" * 65}, {"request_hash": ""}])
def test_operacao_estoque_check_rejeita_tipo_ou_hash_invalidos(migrated, changes):
    values = {"id": str(uuid4()), "tipo": "venda", "request_hash": "a" * 64,
              "resultado": {"ficticio": True}, **changes}
    db.session.add(OperacaoEstoque(**values))
    with pytest.raises(IntegrityError, match="ck_operacao_"):
        db.session.commit()
    db.session.rollback()
    assert OperacaoEstoque.query.count() == 0


@pytest.mark.parametrize("tipo", ["entrada", "saida", "ajuste", "venda"])
@pytest.mark.parametrize("quantidade", [Decimal("-1"), Decimal("0"), Decimal("0.25")])
def test_movimentacao_exige_quantidade_positiva(migrated, tipo, quantidade):
    _, product_id, _ = migrated
    row = MovimentacaoEstoque(
        tipo_item="produto", produto_id=product_id, tipo_movimentacao=tipo,
        quantidade=quantidade, saldo_anterior=1, saldo_posterior=1,
        motivo="Movimentacao ficticia",
    )
    db.session.add(row)
    if quantidade <= 0:
        with pytest.raises(IntegrityError, match="ck_movimentacao_quantidade_positiva"):
            db.session.commit()
        db.session.rollback()
        assert MovimentacaoEstoque.query.count() == 0
    else:
        db.session.commit()
        db.session.remove()
        assert MovimentacaoEstoque.query.one().quantidade == quantidade


@pytest.mark.parametrize("quantidade", [-1, 0])
def test_check_quantidade_tambem_existe_no_create_all(app, catalogo, quantidade):
    with app.app_context():
        db.session.add(MovimentacaoEstoque(
            tipo_item="produto", produto_id=next(iter(catalogo.values())),
            tipo_movimentacao="entrada", quantidade=quantidade,
            saldo_anterior=0, saldo_posterior=1, motivo="Teste ficticio",
        ))
        with pytest.raises(IntegrityError, match="ck_movimentacao_quantidade_positiva"):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("operation", ["insert", "update"])
@pytest.mark.parametrize("quantity", [-1, 0, 5])
def test_produto_exige_saldo_nao_negativo(migrated, operation, quantity):
    _, product_id, _ = migrated
    if operation == "insert":
        product = Produto(nome="Novo Produto Ficticio", preco=1, quantidade_disponivel=quantity)
        db.session.add(product)
    else:
        product = db.session.get(Produto, product_id)
        product.quantidade_disponivel = quantity
    if quantity < 0:
        with pytest.raises(IntegrityError, match="ck_produto_saldo"):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(Produto, product_id).quantidade_disponivel == 20
    else:
        db.session.commit()
        db.session.refresh(product)
        assert product.quantidade_disponivel == quantity


def test_check_saldo_produto_tambem_existe_no_create_all(app):
    with app.app_context():
        db.session.add(Produto(nome="Produto Ficticio Negativo", preco=1, quantidade_disponivel=-1))
        with pytest.raises(IntegrityError, match="ck_produto_saldo"):
            db.session.commit()
        db.session.rollback()
