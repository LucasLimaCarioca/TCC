"""Equivalências declaradas somente sobre registros e códigos inventados."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.database import db
from app.migrations import _config
from app.models.catalogacao_historica import OperacaoCatalogacao, RevisaoAssociacaoHistorica
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.models.venda_historica import VendaHistorica
from app.services import catalogacao_service as catalog
from test_catalogacao_historica import reverse_payload


@pytest.fixture
def experimental_catalog(app, catalogo):
    with app.app_context():
        for index, code in enumerate(("00871", "00952")):
            db.session.add(VendaHistorica(codigo_produto=code, data=date(2026, 1, 2),
                codigo_grupo="GRUPO-FICTICIO", categoria="Embalagem fictícia",
                quantidade=Decimal("1.123456789"), numero_pedidos=1,
                source_hash=str(index+1)*64, dataset_hash="a"*64))
        db.session.commit()
    return catalogo["caixa de 10L - chocolate"], catalogo["caixa de 5L - chocolate"]


def compatibility(**overrides):
    return {"identidade_confirmada": True, "embalagem_confirmada": True,
        "unidade_confirmada": True, "descricao_historica": "Sorvete fictício de chocolate",
        "embalagem_historica": "Caixa de 10L", "unidade_historica": "caixa",
        "unidade_simulada": "caixa", **overrides}


def request(product, *, code="00871", action="associar", selected=True, **overrides):
    listing = catalog.listar_catalogacao()
    code_row = next(row for row in listing["codigos"] if row["codigo_produto"] == code)
    product_row = next(row for row in listing["produtos"] if row["id"] == product)
    return {"acao": action, "produto_id": product, "codigo_produto": code,
        "versao": code_row["versao"], "versao_produto": product_row["versao"],
        "tipo_vinculo": "experimental", "selecionado_previsao": selected,
        "produto_ficticio_confirmado": True, "compatibilidade": compatibility(),
        "confirmar": True, "operador": "Operador fictício", "motivo": "Conferência experimental fictícia.",
        "operacao_id": str(uuid4()), **overrides}


@pytest.mark.parametrize("demonstration", [False, True])
def test_associacao_experimental_preserva_codigo_historico_e_estoque_simulado(app, experimental_catalog, demonstration):
    with app.app_context():
        product = db.session.get(Produto, experimental_catalog[0])
        product.demonstracao = demonstration
        db.session.commit()
        before = db.session.execute(VendaHistorica.__table__.select().order_by(VendaHistorica.id)).all()
        original = (product.id, product.nome, product.preco, product.quantidade_disponivel, product.estoque_minimo)
        receipt = catalog.executar_catalogacao(**request(product.id))
        assert receipt["produto_ids"] == [product.id]
        assert product.codigo_externo == "00871"
        assert str(product.id) != "871"
        assert (product.id, product.nome, product.preco, product.quantidade_disponivel, product.estoque_minimo) == original
        assert catalog.associacao_revisada(db.session, product)
        review = db.session.get(RevisaoAssociacaoHistorica, product.id)
        assert review.tipo_vinculo == "experimental" and review.selecionado_previsao
        context = catalog.snapshot_selecao_experimental(db.session)
        assert context["origem_historico"] == "real" and context["estoque"] == "simulado"
        assert context["vendas_operacionais"] == "simuladas"
        assert context["associacoes"] == [{"produto_id": product.id, "codigo_produto": "00871",
                                           "versao_revisao": catalog.versao_produto(db.session, product)}]
        assert catalog.validar_selecao_experimental(db.session, context)
        assert db.session.execute(VendaHistorica.__table__.select().order_by(VendaHistorica.id)).all() == before
        assert MovimentacaoEstoque.query.count() == 0
        assert "experimental" in catalog.motivo_associacao(db.session, product)
        audit = next(item for item in catalog.listar_catalogacao()["operacoes"]
                     if item["id"] == receipt["operacao_id"])
        assert audit["tipos_vinculo"] == ["experimental"]


def test_vinculo_valido_nao_selecionado_nao_participa_de_treinamento(app, experimental_catalog):
    with app.app_context():
        catalog.executar_catalogacao(**request(experimental_catalog[0], selected=False))
        assert catalog.associacao_revisada(db.session, db.session.get(Produto, experimental_catalog[0]))
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.snapshot_selecao_experimental(db.session)
        assert error.value.code == "SELECTION_EMPTY"


@pytest.mark.parametrize("changes,code", [
    ({"identidade_confirmada": False}, "INCOMPATIBLE_ASSOCIATION"),
    ({"embalagem_confirmada": False}, "INCOMPATIBLE_ASSOCIATION"),
    ({"unidade_confirmada": False}, "INCOMPATIBLE_ASSOCIATION"),
    ({"unidade_historica": "kg"}, "INCOMPATIBLE_ASSOCIATION"),
    ({"unidade_historica": "caixa/2", "unidade_simulada": "caixa2"}, "INCOMPATIBLE_ASSOCIATION"),
    ({"embalagem_historica": "Caixa de 5L"}, "INCOMPATIBLE_ASSOCIATION"),
    ({"descricao_historica": ""}, "INVALID_INPUT"),
])
def test_incompatibilidade_nao_associa_nem_altera_historico(app, experimental_catalog, changes, code):
    with app.app_context():
        data = request(experimental_catalog[0], compatibilidade=compatibility(**changes))
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**data)
        assert error.value.code == code
        assert db.session.get(Produto, experimental_catalog[0]).codigo_externo is None
        assert RevisaoAssociacaoHistorica.query.count() == OperacaoCatalogacao.query.count() == 0
        assert VendaHistorica.query.count() == 2


@pytest.mark.parametrize("overrides,code", [
    ({"produto_ficticio_confirmado": False}, "CONFIRMATION_REQUIRED"),
    ({"compatibilidade": None}, "INCOMPATIBLE_ASSOCIATION"),
    ({"tipo_vinculo": []}, "INVALID_INPUT"),
    ({"tipo_vinculo": "operacional"}, "INVALID_INPUT"),
    ({"selecionado_previsao": "true"}, "INVALID_INPUT"),
])
def test_experimento_exige_natureza_e_confirmacoes_explicitas(app, experimental_catalog, overrides, code):
    with app.app_context(), pytest.raises(catalog.CatalogacaoError) as error:
        catalog.executar_catalogacao(**request(experimental_catalog[0], **overrides))
    assert error.value.code == code


def test_apenas_perfis_dos_onze_produtos_existentes_podem_receber_vinculo_experimental(app, experimental_catalog):
    with app.app_context():
        other = Produto(nome="Outro produto fictício fora dos onze", categoria="Outro formato", sabor="Outro sabor", preco=1)
        db.session.add(other)
        db.session.commit()
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**request(other.id))
        assert error.value.code == "EXPERIMENTAL_PRODUCT_REQUIRED"
        assert other.codigo_externo is None
        listed = catalog.listar_catalogacao()
        assert not next(row for row in listed["produtos"] if row["id"] == other.id)["elegivel_experimento"]


def test_criar_produto_nao_e_uma_operacao_experimental(app, experimental_catalog):
    with app.app_context(), pytest.raises(catalog.CatalogacaoError) as error:
        data = request(experimental_catalog[0], action="criar")
        del data["produto_id"], data["versao_produto"]
        data["produto"] = {"nome": "Não deve ser criado"}
        catalog.executar_catalogacao(**data)
    assert error.value.code == "EXPERIMENTAL_CREATE_DISABLED"


def test_selecao_auditada_reversivel_invalidacao_e_preservacao_do_saldo(app, experimental_catalog):
    with app.app_context():
        product = experimental_catalog[0]
        catalog.executar_catalogacao(**request(product))
        context = catalog.snapshot_selecao_experimental(db.session)
        removed = catalog.executar_catalogacao(**request(product, action="revisar", selected=False))
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.validar_selecao_experimental(db.session, context)
        assert error.value.code == "SELECTION_CHANGED"
        assert catalog.associacao_revisada(db.session, db.session.get(Produto, product))
        catalog.executar_catalogacao(**reverse_payload(removed["operacao_id"]))
        assert catalog.snapshot_selecao_experimental(db.session) == context
        assert db.session.get(Produto, product).quantidade_disponivel == 20
        assert MovimentacaoEstoque.query.count() == 0


def test_alteracao_comercial_invalida_selecao_sem_apagar_revisao(app, experimental_catalog):
    with app.app_context():
        product_id = experimental_catalog[0]
        catalog.executar_catalogacao(**request(product_id))
        product = db.session.get(Produto, product_id)
        product.sabor = "Outro sabor não conferido"
        db.session.commit()
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.snapshot_selecao_experimental(db.session)
        assert error.value.code == "SELECTION_INVALID"
        assert db.session.get(RevisaoAssociacaoHistorica, product_id).selecionado_previsao


def test_selecao_inativa_e_rejeitada(app, experimental_catalog):
    with app.app_context():
        product = db.session.get(Produto, experimental_catalog[0])
        product.ativo = False
        db.session.commit()
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**request(product.id))
        assert error.value.code == "SELECTION_INVALID"


def test_historico_sem_associacao_nao_entra_na_selecao_e_contexto_nao_aceita_adulteracao(app, experimental_catalog):
    with app.app_context():
        catalog.executar_catalogacao(**request(experimental_catalog[0]))
        selected = catalog.snapshot_selecao_experimental(db.session)
        assert [row["codigo_produto"] for row in selected["associacoes"]] == ["00871"]
        tampered = deepcopy(selected)
        tampered["associacoes"][0]["codigo_produto"] = "871"
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.validar_selecao_experimental(db.session, tampered)
        assert error.value.code == "SELECTION_INVALID"


@pytest.mark.parametrize("action", ["revisar", "alterar"])
def test_cliente_legado_nao_converte_vinculo_experimental_implicitamente(app, experimental_catalog, action):
    with app.app_context():
        origin, other = experimental_catalog
        catalog.executar_catalogacao(**request(origin))
        before = catalog.snapshot_selecao_experimental(db.session)
        data = request(origin if action == "revisar" else other, action=action)
        for key in ("tipo_vinculo", "selecionado_previsao", "produto_ficticio_confirmado", "compatibilidade"):
            del data[key]
        with pytest.raises(catalog.CatalogacaoError) as error:
            catalog.executar_catalogacao(**data)
        assert error.value.code == "EXPERIMENTAL_MODE_REQUIRED"
        assert catalog.snapshot_selecao_experimental(db.session) == before
        assert db.session.get(Produto, origin).codigo_externo == "00871"
        assert db.session.get(Produto, other).codigo_externo is None


def test_falha_auditoria_faz_rollback_do_modo_selecao_e_vinculo(app, experimental_catalog):
    def fail(*args):
        raise RuntimeError("Falha fictícia da auditoria experimental")
    with app.app_context():
        event.listen(OperacaoCatalogacao, "before_insert", fail)
        try:
            with pytest.raises(RuntimeError, match="Falha fictícia"):
                catalog.executar_catalogacao(**request(experimental_catalog[0]))
        finally:
            event.remove(OperacaoCatalogacao, "before_insert", fail)
        assert db.session.get(Produto, experimental_catalog[0]).codigo_externo is None
        assert RevisaoAssociacaoHistorica.query.count() == 0


def test_auditoria0009_permanece_reversivel_sem_reescrever_snapshot_legado(app, experimental_catalog):
    with app.app_context():
        data = request(experimental_catalog[0], selected=False)
        for key in ("tipo_vinculo", "selecionado_previsao", "produto_ficticio_confirmado", "compatibilidade"):
            del data[key]
        receipt = catalog.executar_catalogacao(**data)
        operation = db.session.get(OperacaoCatalogacao, receipt["operacao_id"])
        legacy = deepcopy(operation.depois_json)
        for snapshot in legacy:
            if snapshot["revisao"]:
                for key in ("tipo_vinculo", "selecionado_previsao", "compatibilidade"):
                    del snapshot["revisao"][key]
        operation.depois_json = legacy
        db.session.commit()
        listed = catalog.listar_catalogacao()
        assert next(item for item in listed["operacoes"] if item["id"] == operation.id)["reversivel"]
        catalog.executar_catalogacao(**reverse_payload(operation.id))
        assert db.session.get(Produto, experimental_catalog[0]).codigo_externo is None
        assert db.session.get(OperacaoCatalogacao, operation.id).depois_json == legacy


def test_migracao0010_preserva_revisao_auditoria_produto_e_historico(tmp_path):
    from app.app import create_app
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'legado.db'}"})
    with app.app_context(), db.engine.begin() as con:
        command.upgrade(_config(con), "0009_catalogacao_historica")
        con.exec_driver_sql("INSERT INTO produtos (id,nome,categoria,sabor,preco,quantidade_disponivel,ativo,codigo_externo) VALUES (91,'Produto legado fictício','Categoria fictícia','Sabor fictício',2,17,1,'00871')")
        con.exec_driver_sql("INSERT INTO revisoes_associacoes_historicas VALUES (91,'00871',?,?,'Operador fictício','Conferência legada','2026-01-01')", ("a"*64,"b"*64))
        before_product = con.exec_driver_sql("SELECT * FROM produtos").all()
        before_review = con.exec_driver_sql("SELECT * FROM revisoes_associacoes_historicas").all()
        command.upgrade(_config(con), "0010_associacoes_experimentais")
        assert con.exec_driver_sql("SELECT * FROM produtos").all() == before_product
        after = con.exec_driver_sql("SELECT * FROM revisoes_associacoes_historicas").all()
        assert [row[:-3] for row in after] == before_review
        assert after[0][-3:] == ("operacional", 0, None)
        assert compare_metadata(MigrationContext.configure(con), db.metadata) == []
        assert con.exec_driver_sql("PRAGMA foreign_key_check").all() == []
