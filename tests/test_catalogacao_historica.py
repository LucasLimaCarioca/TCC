"""Catalogação exclusivamente fictícia; sem ler CSV, banco ou artefato real."""

from datetime import date
from decimal import Decimal
import json
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import db
from app.migrations import _config
from app.models.alerta_estoque import AlertaEstoque
from app.models.catalogacao_historica import OperacaoCatalogacao, RevisaoAssociacaoHistorica
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.models.venda_historica import VendaHistorica
from app.services import catalogacao_service as catalog
from app.services import estoque_service
from test_forecasting_service import project


@pytest.fixture
def inventory(app):
    with app.app_context():
        db.session.add_all([
            VendaHistorica(codigo_produto="00770", data=date(2026, 1, 2), codigo_grupo="GRUPO-FICTICIO",
                categoria="Formato fictício A", quantidade=Decimal("4.123456789"), numero_pedidos=2,
                source_hash="a"*64, dataset_hash="b"*64),
            VendaHistorica(codigo_produto="00880", data=date(2026, 1, 2), codigo_grupo="GRUPO-FICTICIO",
                categoria="Formato fictício B", quantidade=Decimal("3"), numero_pedidos=1,
                source_hash="c"*64, dataset_hash="b"*64),
        ])
        products = [Produto(nome="Produto fictício A", categoria="Formato fictício A", sabor="Sabor A",
                            preco=12.5, quantidade_disponivel=8, estoque_minimo=2),
                    Produto(nome="Produto fictício B", categoria="Formato fictício B", sabor="Sabor B",
                            preco=9, quantidade_disponivel=6, estoque_minimo=1)]
        db.session.add_all(products)
        db.session.commit()
        return [product.id for product in products]


def payload(code="00770", action="associar", product=None):
    state = catalog.listar_catalogacao()
    entry = next(item for item in state["codigos"] if item["codigo_produto"] == code)
    data = {"acao": action, "codigo_produto": code, "versao": entry["versao"],
            "confirmar": True, "operador": "Operador fictício", "motivo": "Conferência comercial fictícia.",
            "operacao_id": str(uuid4())}
    if product is not None:
        target = next(item for item in state["produtos"] if item["id"] == product)
        data.update(produto_id=product, versao_produto=target["versao"])
    return data


def new_product(**overrides):
    return {"nome": "Novo produto fictício", "categoria": "Formato informado pelo operador",
            "sabor": "Sabor fictício", "preco": "10,25", "estoque_inicial": "3",
            "estoque_minimo": "4", **overrides}


def historical_rows():
    return db.session.execute(VendaHistorica.__table__.select().order_by(VendaHistorica.id)).all()


def test_listagem_sem_nome_associacao_ou_escrita_preserva_codigo_e_historico(app, inventory):
    with app.app_context():
        before = historical_rows()
        result = catalog.listar_catalogacao()
        code = next(item for item in result["codigos"] if item["codigo_produto"] == "00770")
        assert code["estado"] == "sem_associacao"
        assert code["nome"] is None and code["produto_id"] is None
        assert code["categorias"] == ["Formato fictício A"]
        assert code["grupos"] == ["GRUPO-FICTICIO"]
        assert len(code["versao"]) == 64
        assert historical_rows() == before
        assert RevisaoAssociacaoHistorica.query.count() == OperacaoCatalogacao.query.count() == 0


def test_associacao_manual_preserva_zeros_e_nao_deriva_codigo_do_id(app, inventory):
    with app.app_context():
        before = historical_rows()
        request = payload(product=inventory[1])
        result = catalog.executar_catalogacao(**request)
        product = db.session.get(Produto, inventory[1])
        assert product.codigo_externo == "00770"
        assert str(product.id) != "770"
        assert catalog.associacao_revisada(db.session, product)
        assert historical_rows() == before
        entry = next(item for item in catalog.listar_catalogacao()["codigos"] if item["codigo_produto"] == "00770")
        # Divergência de categoria não prova erro: revisão expressa resolve pendência.
        assert entry["estado"] == "revisado" and entry["avisos"]
        assert result["produto_ids"] == [product.id]
        operation = db.session.get(OperacaoCatalogacao, result["operacao_id"])
        assert operation.operador == "Operador fictício"
        assert operation.antes_json[0]["codigo_externo"] is None
        assert operation.depois_json[0]["codigo_externo"] == "00770"


def test_vinculo_legado_divergente_permanece_intacto_pendente_ate_revisao(app, inventory):
    with app.app_context():
        product = db.session.get(Produto, inventory[1])
        product.codigo_externo = "00770"
        db.session.commit()
        entry = next(item for item in catalog.listar_catalogacao()["codigos"] if item["codigo_produto"] == "00770")
        assert entry["estado"] == "possivel_inconsistencia" and not entry["revisado"]
        assert product.codigo_externo == "00770"
        assert not catalog.associacao_revisada(db.session, product)
        catalog.executar_catalogacao(**payload(action="revisar", product=product.id))
        assert catalog.associacao_revisada(db.session, product)


@pytest.mark.parametrize("field,value", [("nome", "Nome fictício alterado"), ("categoria", "Outra categoria fictícia"),
    ("sabor", "Outro sabor fictício"), ("preco", 15), ("ativo", False), ("codigo_externo", "00880")])
def test_alteracao_comercial_invalida_revisao_sem_mutacao_na_consulta(app, inventory, field, value):
    with app.app_context():
        catalog.executar_catalogacao(**payload(product=inventory[0]))
        product = db.session.get(Produto, inventory[0])
        setattr(product, field, value)
        db.session.commit()
        old = RevisaoAssociacaoHistorica.query.one().revisado_em
        assert not catalog.associacao_revisada(db.session, product)
        assert RevisaoAssociacaoHistorica.query.one().revisado_em == old


def test_saldos_e_minimos_nao_invalidam_revisao_de_identidade(app, inventory):
    with app.app_context():
        catalog.executar_catalogacao(**payload(product=inventory[0]))
        product = db.session.get(Produto, inventory[0])
        product.quantidade_disponivel, product.estoque_minimo = 30, 7
        db.session.commit()
        assert catalog.associacao_revisada(db.session, product)


def test_mudanca_de_metadados_historicos_exige_nova_revisao_sem_reescrever_vendas(app, inventory):
    with app.app_context():
        catalog.executar_catalogacao(**payload(product=inventory[0]))
        row = VendaHistorica.query.filter_by(codigo_produto="00770").one()
        row.categoria = "Nova classificação fictícia importada"
        db.session.commit()
        assert not catalog.associacao_revisada(db.session, db.session.get(Produto, inventory[0]))


def test_produto_novo_tem_movimentacao_inicial_alerta_transacao_e_inativo_por_padrao(app, inventory):
    with app.app_context():
        before = historical_rows()
        data = payload(action="criar")
        data["produto"] = new_product()
        result = catalog.executar_catalogacao(**data)
        product = db.session.get(Produto, result["produto_ids"][0])
        assert product.codigo_externo == "00770" and product.quantidade_disponivel == 3
        assert product.estoque_minimo == 4 and product.preco == 10.25
        assert product.ativo is False and product.demonstracao is False
        movement = MovimentacaoEstoque.query.filter_by(produto_id=product.id).one()
        assert movement.tipo_movimentacao == "entrada"
        assert movement.quantidade == Decimal(3) and movement.saldo_anterior == Decimal(0)
        assert movement.saldo_posterior == Decimal(3) and "Cadastro inicial" in movement.motivo
        assert AlertaEstoque.query.filter_by(produto_id=product.id, ativo=True).count() == 0
        assert historical_rows() == before


def test_criacao_ativa_explicita_gera_alerta_e_zero_nao_gera_movimento(app, inventory):
    with app.app_context():
        data = payload(action="criar")
        data["produto"] = new_product(ativo=True, estoque_inicial="0")
        result = catalog.executar_catalogacao(**data)
        product_id = result["produto_ids"][0]
        assert MovimentacaoEstoque.query.filter_by(produto_id=product_id).count() == 0
        assert AlertaEstoque.query.filter_by(produto_id=product_id, ativo=True).count() == 1


@pytest.mark.parametrize("field,value", [("estoque_inicial", "1.5"), ("estoque_inicial", -1),
    ("estoque_inicial", True), ("estoque_minimo", "0.2"), ("preco", "NaN"),
    ("preco", "-1"), ("ativo", "true"), ("nome", ""), ("categoria", ""), ("sabor", "")])
def test_criacao_invalida_nao_cria_produto_entrada_revisao_ou_auditoria(app, inventory, field, value):
    with app.app_context():
        data = payload(action="criar")
        data["produto"] = new_product(**{field: value})
        with pytest.raises(estoque_service.EstoqueError):
            catalog.executar_catalogacao(**data)
        assert Produto.query.count() == 2
        assert MovimentacaoEstoque.query.count() == RevisaoAssociacaoHistorica.query.count() == OperacaoCatalogacao.query.count() == 0


@pytest.mark.parametrize("model", [MovimentacaoEstoque, RevisaoAssociacaoHistorica, OperacaoCatalogacao])
def test_falha_de_persistencia_reverte_produto_saldo_movimento_revisao_alerta_e_auditoria(app, inventory, model):
    def fail(*args):
        raise RuntimeError("Falha fictícia de persistência.")

    with app.app_context():
        data = payload(action="criar")
        data["produto"] = new_product(ativo=True)
        event.listen(model, "before_insert", fail)
        try:
            with pytest.raises(RuntimeError, match="Falha fictícia"):
                catalog.executar_catalogacao(**data)
        finally:
            event.remove(model, "before_insert", fail)
        assert Produto.query.count() == 2
        assert MovimentacaoEstoque.query.count() == RevisaoAssociacaoHistorica.query.count() == 0
        assert AlertaEstoque.query.count() == OperacaoCatalogacao.query.count() == 0


def test_reenvio_idempotente_do_cadastro_nao_duplica_produto_ou_saldo(app, inventory):
    with app.app_context():
        data = payload(action="criar")
        data["produto"] = new_product(ativo=True)
        first = catalog.executar_catalogacao(**data)
        assert catalog.executar_catalogacao(**data) == first
        assert Produto.query.count() == 3 and MovimentacaoEstoque.query.count() == OperacaoCatalogacao.query.count() == 1
        changed = {**data, "motivo": "Outra justificativa fictícia."}
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**changed)
        assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_falha_de_commit_reverte_toda_catalogacao(app, inventory, monkeypatch):
    with app.app_context():
        data = payload(action="criar")
        data["produto"] = new_product(ativo=True)
        before = historical_rows()

        def fail_commit(self):
            raise SQLAlchemyError("Falha fictícia de commit.")

        with monkeypatch.context() as patch:
            patch.setattr(Session, "commit", fail_commit)
            with pytest.raises(catalog.CatalogacaoError) as error:
                catalog.executar_catalogacao(**data)
        assert error.value.code == "STORAGE_ERROR"
        assert Produto.query.count() == 2
        assert historical_rows() == before
        assert all(model.query.count() == 0 for model in
                   (MovimentacaoEstoque, RevisaoAssociacaoHistorica, AlertaEstoque, OperacaoCatalogacao))


def test_produto_demo_nao_recebe_codigo_e_revisao_nao_e_valida(app, inventory):
    with app.app_context():
        product = db.session.get(Produto, inventory[0])
        product.demonstracao = True
        db.session.commit()
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**payload(product=product.id))
        assert error.value.code == "DEMONSTRATION_PRODUCT"
        assert product.codigo_externo is None
        assert not catalog.associacao_revisada(db.session, product)


def test_versao_obsoleta_nao_sobrescreve_vinculo_ou_metadados(app, inventory):
    with app.app_context():
        request = payload(product=inventory[0])
        product = db.session.get(Produto, inventory[0])
        product.nome = "Nome comercial fictício revisado"
        db.session.commit()
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**request)
        assert error.value.code == "CATALOG_CHANGED"
        assert product.codigo_externo is None


def test_codigos_e_produtos_nao_aceitam_associacoes_conflitantes(app, inventory):
    with app.app_context():
        catalog.executar_catalogacao(**payload(product=inventory[0]))
        for request in [payload(product=inventory[1]), payload(code="00880", product=inventory[0])]:
            with pytest.raises(catalog.CatalogacaoError) as error:
                catalog.executar_catalogacao(**request)
            assert error.value.code == "MAPPING_CONFLICT"
        assert db.session.get(Produto, inventory[0]).codigo_externo == "00770"
        assert db.session.get(Produto, inventory[1]).codigo_externo is None


def reverse_payload(operation_id):
    state = catalog.listar_catalogacao()
    original = next(item for item in state["operacoes"] if item["id"] == operation_id)
    return {"acao": "reverter", "operacao_id": str(uuid4()), "reverter_operacao_id": operation_id,
        "versoes": {str(product["id"]): product["versao"] for product in state["produtos"] if product["id"] in original["produtos"]},
        "confirmar": True, "operador": "Operador fictício", "motivo": "Reversão conferida fictícia."}


def test_alteracao_auditavel_e_reversao_restauram_vinculo_sem_mudar_historico_estoque_ou_previsoes(app, inventory):
    with app.app_context():
        first = catalog.executar_catalogacao(**payload(product=inventory[0]))
        before = historical_rows()
        result = catalog.executar_catalogacao(**payload(action="alterar", product=inventory[1]))
        assert db.session.get(Produto, inventory[0]).codigo_externo is None
        assert db.session.get(Produto, inventory[1]).codigo_externo == "00770"
        assert catalog.associacao_revisada(db.session, db.session.get(Produto, inventory[1]))
        catalog.executar_catalogacao(**reverse_payload(result["operacao_id"]))
        old, new = (db.session.get(Produto, key) for key in inventory)
        assert old.codigo_externo == "00770" and new.codigo_externo is None
        assert (old.quantidade_disponivel, new.quantidade_disponivel) == (8, 6)
        assert catalog.associacao_revisada(db.session, old)
        assert historical_rows() == before and MovimentacaoEstoque.query.count() == 0
        assert OperacaoCatalogacao.query.count() == 3
        audit = catalog.listar_catalogacao()["operacoes"]
        assert next(item for item in audit if item["id"] == result["operacao_id"])["reversivel"] is False
        assert next(item for item in audit if item["id"] == first["operacao_id"])["reversivel"] is True


def test_reverter_criacao_desfaz_somente_vinculo_preservando_produto_saldo_e_entrada(app, inventory):
    with app.app_context():
        data = payload(action="criar")
        data["produto"] = new_product()
        result = catalog.executar_catalogacao(**data)
        catalog.executar_catalogacao(**reverse_payload(result["operacao_id"]))
        product = db.session.get(Produto, result["produto_ids"][0])
        assert product is not None and product.codigo_externo is None and product.quantidade_disponivel == 3
        assert MovimentacaoEstoque.query.filter_by(produto_id=product.id).count() == 1


def test_reversao_obsoleta_ou_duplicada_nao_sobrescreve_acao_posterior(app, inventory):
    with app.app_context():
        result = catalog.executar_catalogacao(**payload(product=inventory[0]))
        old_reverse = reverse_payload(result["operacao_id"])
        catalog.executar_catalogacao(**payload(action="revisar", product=inventory[0]))
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**old_reverse)
        assert error.value.code == "CATALOG_CHANGED"


def test_segunda_reversao_da_mesma_operacao_e_rejeitada(app, inventory):
    with app.app_context():
        result = catalog.executar_catalogacao(**payload(product=inventory[0]))
        request = reverse_payload(result["operacao_id"])
        catalog.executar_catalogacao(**request)
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**{**request, "operacao_id": str(uuid4())})
        assert error.value.code == "MAPPING_CONFLICT"
        assert db.session.get(Produto, inventory[0]).codigo_externo is None


@pytest.mark.parametrize("changes,code", [({"confirmar": False}, "CONFIRMATION_REQUIRED"),
    ({"operador": ""}, "INVALID_INPUT"), ({"motivo": ""}, "INVALID_INPUT"),
    ({"acao": []}, "INVALID_INPUT"), ({"codigo_produto": 770}, "INVALID_INPUT"),
    ({"codigo_produto": "770"}, "HISTORY_NOT_FOUND"), ({"codigo_produto": " 00770"}, "INVALID_INPUT")])
def test_operacoes_exigem_confirmacao_motivo_operador_e_codigo_textual_exato(app, inventory, changes, code):
    with app.app_context():
        request = {**payload(product=inventory[0]), **changes}
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**request)
        assert error.value.code == code


def test_duas_associacoes_concorrentes_nao_compartilham_codigo(app, inventory):
    with app.app_context():
        requests = [payload(product=key) for key in inventory]
    barrier = Barrier(2)

    def apply(request):
        with app.app_context():
            barrier.wait()
            try:
                catalog.executar_catalogacao(**request)
                return "ok"
            except catalog.CatalogacaoError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(apply, requests))
    assert sorted(outcomes) == ["CATALOG_CHANGED", "ok"]
    with app.app_context():
        assert Produto.query.filter_by(codigo_externo="00770").count() == 1
        assert RevisaoAssociacaoHistorica.query.count() == OperacaoCatalogacao.query.count() == 1


def test_api_de_mutacao_passa_pela_autoridade_estoque_e_respeita_conflitos(app, inventory, monkeypatch):
    calls = []

    def execute(command, **data):
        calls.append(command)
        return catalog.executar_catalogacao(**data)

    monkeypatch.setattr("app.routes.catalogacao_routes.EstoqueClient.executar", lambda self, command, **data: execute(command, **data))
    client = app.test_client()
    with app.app_context():
        request = payload(product=inventory[0])
    response = client.post("/api/catalogacao/operacoes", json=request)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == ["catalogar_historico"]
    listed = client.get("/api/catalogacao")
    assert listed.status_code == 200 and listed.headers["Cache-Control"] == "no-store"
    assert next(item for item in listed.json["codigos"] if item["codigo_produto"] == "00770")["revisado"]
    response = client.post("/api/catalogacao/operacoes", json={**request, "operacao_id": str(uuid4())})
    assert response.status_code == 409 and response.json["code"] == "CATALOG_CHANGED"


def test_catalogo_inclui_codigos_da_publicacao_sem_exigir_importacao_no_banco(app, project):
    from test_forecasting_service import execute
    from app.services.previsao_service import PrevisaoService

    root, review = project
    app.config.update(FORECAST_PROJECT_ROOT=root, FORECAST_REVIEW=str(review), FORECAST_START="2026-01-01")
    paths = execute(project)
    PrevisaoService(app).carregar(paths["manifest"])
    with app.app_context():
        result = catalog.listar_catalogacao()
        assert VendaHistorica.query.count() == 0
        assert {item["codigo_produto"] for item in result["codigos"]} == {"00110", "00220"}
        assert all(item["estado"] == "sem_associacao" for item in result["codigos"])
        assert all(item["categorias"] == ["Grupo Fictício"] for item in result["codigos"])


def test_migracao_incremental_preserva_vinculos_sem_aprovar_e_schema_equivale_modelos(tmp_path):
    from app.app import create_app
    application = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'ficticio.db'}"})
    with application.app_context():
        with db.engine.begin() as connection:
            command.upgrade(_config(connection), "0008_alertas_preventivos")
            connection.exec_driver_sql("INSERT INTO produtos (id,nome,categoria,sabor,preco,quantidade_disponivel,ativo,codigo_externo) VALUES (41,'Produto legado fictício','Formato fictício','Sabor fictício',7,13,1,'00770')")
            command.upgrade(_config(connection), "0009_catalogacao_historica")
            assert connection.exec_driver_sql("SELECT id,nome,preco,quantidade_disponivel,ativo,codigo_externo,demonstracao FROM produtos").one() == (41,"Produto legado fictício",7,13,1,"00770",0)
            assert connection.exec_driver_sql("SELECT count(*) FROM revisoes_associacoes_historicas").scalar() == 0
            assert connection.exec_driver_sql("SELECT count(*) FROM operacoes_catalogacao").scalar() == 0
            assert compare_metadata(MigrationContext.configure(connection), db.metadata) == []
        db.session.remove()
        db.engine.dispose()
