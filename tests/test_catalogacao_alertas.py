"""Revisão de vínculos e alertas com publicações e bancos inteiramente fictícios."""

from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents import estoque_agent
from app.agents.gateway import AgentGateway
from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.catalogacao_historica import RevisaoAssociacaoHistorica
from app.models.produto import Produto
from app.models.venda_historica import VendaHistorica
from app.services import alerta_preventivo_service as preventive
from app.services import catalogacao_service as catalog
from app.services import estoque_service
from app.services.estoque_service import EstoqueError
from app.services.previsao_service import PrevisaoError
from test_agent_runtime import runtime
from test_alerta_preventivo_service import TODAY, edit_product, ready, snapshot_alert, sync
from test_forecasting_service import project
from test_previsao_service import configured


def files(service):
    return {path: path.read_bytes() for path in (service.root / "data/artifacts").rglob("*") if path.is_file()}


def revoke_review(service, product):
    with service.app.app_context():
        review = db.session.get(RevisaoAssociacaoHistorica, product)
        assert review is not None
        db.session.delete(review)
        db.session.commit()


def unmap_with_confirmation(service, product):
    """A própria operação administrativa é explícita, versionada e auditável."""
    with service.app.app_context():
        state = catalog.listar_catalogacao()
        entry = next(row for row in state["codigos"] if row["produto_id"] == product)
        target = next(row for row in state["produtos"] if row["id"] == product)
        catalog.executar_catalogacao("desassociar", codigo_produto=entry["codigo_produto"],
            versao=entry["versao"], produto_id=product, versao_produto=target["versao"],
            confirmar=True, operador="Operador fictício", motivo="Revisão fictícia durante o RPC.",
            operacao_id=str(uuid4()))


def test_legado_sem_revisao_bloqueia_previsao_por_produto_mas_preserva_consulta_por_codigo(configured):
    service, paths, (product, _, _) = configured
    service.carregar(paths["manifest"])
    original = files(service)
    revoke_review(service, product)
    panel = service.painel(codigo_produto="00110")
    general = service.consultar()
    with pytest.raises(PrevisaoError) as error:
        service.consultar(product)
    assert error.value.code == "MAPPING_REVIEW_REQUIRED"
    assert service.painel(codigo_produto="00110") == panel
    assert panel["selecao"]["codigo_produto"] == "00110"
    assert panel["periodos"] and panel["total_previsto"]
    assert service.consultar() == general
    assert files(service) == original
    with service.app.app_context():
        assert db.session.get(Produto, product).codigo_externo == "00110"
        assert AlertaEstoque.query.count() == 0


@pytest.mark.parametrize("field,value", [
    ("nome", "Nome comercial fictício alterado"),
    ("categoria", "Categoria operacional fictícia alterada"),
    ("demonstracao", True),
])
def test_mudanca_da_identidade_comercial_ou_origem_invalida_aprovacao(configured, field, value):
    service, paths, (product, _, _) = configured
    service.carregar(paths["manifest"])
    assert service.consultar(product)["codigo_produto"] == "00110"
    original = files(service)
    with service.app.app_context():
        item = db.session.get(Produto, product)
        review = db.session.get(RevisaoAssociacaoHistorica, product)
        signature = review.assinatura_produto
        setattr(item, field, value)
        db.session.commit()
        assert not catalog.associacao_revisada(db.session, item)
        assert review.assinatura_produto == signature  # Consulta não aprova a alteração.
    with pytest.raises(PrevisaoError) as error:
        service.consultar(product)
    assert error.value.code == "MAPPING_REVIEW_REQUIRED"
    assert service.painel(codigo_produto="00110")["periodos"]
    assert files(service) == original


def test_novo_metadado_historico_exige_revisao_sem_alterar_publicacao(configured):
    service, paths, (product, _, _) = configured
    service.carregar(paths["manifest"])
    original = files(service)
    with service.app.app_context():
        db.session.add(VendaHistorica(codigo_produto="00110", data=date(2026, 1, 2),
            codigo_grupo="GRUPO-FICTICIO-NOVO", categoria="Categoria histórica fictícia nova",
            quantidade=1, numero_pedidos=1, source_hash="c" * 64, dataset_hash="d" * 64))
        db.session.commit()
        old = db.session.get(RevisaoAssociacaoHistorica, product).revisado_em
        assert not catalog.associacao_revisada(db.session, db.session.get(Produto, product))
    with pytest.raises(PrevisaoError) as error:
        service.consultar(product)
    assert error.value.code == "MAPPING_REVIEW_REQUIRED"
    with service.app.app_context():
        assert db.session.get(RevisaoAssociacaoHistorica, product).revisado_em == old
        assert VendaHistorica.query.one().categoria == "Categoria histórica fictícia nova"
    assert files(service) == original


def test_visao_estoque_sinaliza_vinculo_pendente_sem_resolver_ou_reescrever_alerta(ready):
    service, product, _ = ready
    sync(ready)
    before = snapshot_alert(ready)
    revoke_review(service, product)
    with service.app.app_context():
        state = estoque_service.visao_estoque()
    alert = next(row for row in state["alertas"] if row["id"] == before["id"])
    assert alert["ativo"] is True
    assert alert["avaliacao"] == {"atual": False, "motivo": "associacao_pendente_revisao"}
    assert snapshot_alert(ready) == before


@pytest.fixture
def mapping_ready(ready, monkeypatch):
    service, _, _ = ready
    service.app.config["PREDICTIVE_ALERTS_AUTO_ENABLED"] = False
    original_day = preventive._hoje
    monkeypatch.setattr(preventive, "_hoje", lambda value: original_day(TODAY if value is None else value))
    monkeypatch.setattr(estoque_agent, "datetime", SimpleNamespace(
        now=lambda zone: datetime(2026, 1, 11, tzinfo=zone)))
    return ready


def revoke_during_rpc(monkeypatch, runtime, prepared, operation):
    service, product, _ = prepared
    original_query = runtime.agents["previsao"].service.consultar
    replies = []

    def capture_and_revoke(**kwargs):
        result = original_query(**kwargs)  # Previsão verdadeira obtida com revisão válida.
        replies.append(deepcopy(result))
        if operation == "desassociar":
            unmap_with_confirmation(service, product)
        else:
            revoke_review(service, product)
        return result

    monkeypatch.setattr(runtime.agents["previsao"].service, "consultar", capture_and_revoke)
    return replies


@pytest.mark.parametrize("operation,code", [
    ("desassociar", "PRODUCT_UNMAPPED"), ("revogar_revisao", "MAPPING_REVIEW_REQUIRED"),
])
@pytest.mark.parametrize("existing", [False, True])
def test_mudanca_do_vinculo_durante_rpc_nao_cria_nem_resolve_alerta(mapping_ready, runtime, monkeypatch, operation, code, existing):
    service, product, _ = mapping_ready
    if existing:
        sync(mapping_ready)
        original = snapshot_alert(mapping_ready)
        # Sem a revalidação, o saldo maior resolveria indevidamente o alerta.
        edit_product(mapping_ready, quantidade_disponivel=20)
    original_files = files(service)
    replies = revoke_during_rpc(monkeypatch, runtime, mapping_ready, operation)
    with pytest.raises(EstoqueError) as error:
        AgentGateway(runtime).sincronizar_alerta_preventivo(product)
    assert error.value.code == code
    assert len(replies) == 1 and replies[0]["codigo_produto"] == "00110"
    if existing:
        assert snapshot_alert(mapping_ready) == original
    else:
        with service.app.app_context():
            assert AlertaEstoque.query.count() == 0
    assert files(service) == original_files


@pytest.mark.parametrize("operation", ["desassociar", "revogar_revisao"])
def test_consulta_de_risco_revalida_associacao_depois_do_rpc(mapping_ready, runtime, monkeypatch, operation):
    service, product, _ = mapping_ready
    replies = revoke_during_rpc(monkeypatch, runtime, mapping_ready, operation)
    result = AgentGateway(runtime).consultar_risco(product)
    assert len(replies) == 1
    assert result["status"] == "associacao_pendente"
    assert result["code"] == "MAPPING_REVIEW_REQUIRED" and result["risco"] is None
    assert "saldo_projetado" not in result
    with service.app.app_context():
        assert AlertaEstoque.query.count() == 0
