"""Motor, catálogo e agentes integrados sem dados históricos reais."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.agents import estoque_agent
from app.agents.gateway import AgentGateway
from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.previsao_execucao import PrevisaoExecucao
from app.models.produto import Produto
from app.services import alerta_preventivo_service as preventive
from app.services import catalogacao_service as catalog
from app.services import previsao_service
from app.services.estoque_service import EstoqueError
from app.services.previsao_service import PrevisaoError
from experimental_fixtures import experimental_configured, selection_request
from test_agent_runtime import runtime
from test_forecasting_service import project
from test_previsao_service import configured


def test_consulta_legada_e_bloqueada_se_outro_produto_entrar_na_selecao(configured):
    from app.services.estoque_service import conferir_associacao
    service, paths, (legacy_product, target, _) = configured
    service.carregar(paths["manifest"])
    with service.app.app_context():
        assert conferir_associacao(legacy_product, "00110")["confirmada"]
        product = db.session.get(Produto, target)
        product.nome, product.categoria, product.sabor = "caixa de 5L - morango", "caixa de 5L", "morango"
        db.session.commit()
        state = catalog.listar_catalogacao()
        code = next(item for item in state["codigos"] if item["codigo_produto"] == "00220")
        target_state = next(item for item in state["produtos"] if item["id"] == target)
        catalog.executar_catalogacao(acao="associar", produto_id=target, codigo_produto="00220",
            versao=code["versao"], versao_produto=target_state["versao"],
            tipo_vinculo="experimental", selecionado_previsao=True, produto_ficticio_confirmado=True,
            compatibilidade={"identidade_confirmada": True, "embalagem_confirmada": True,
                "unidade_confirmada": True, "descricao_historica": "Morango fictício em caixa",
                "embalagem_historica": "caixa de 5L", "unidade_historica": "caixa", "unidade_simulada": "caixa"},
            confirmar=True, operador="Operador fictício", motivo="Seleção explícita fictícia durante uma consulta legada.")
        result = conferir_associacao(legacy_product, "00110")
        assert result["confirmada"] is False and result["code"] == "EXPERIMENTAL_RUN_REQUIRED"


def deselect(service, product):
    with service.app.app_context():
        return catalog.executar_catalogacao(**selection_request(service, product, selected=False))


def active_id(service):
    with service.app.app_context():
        return PrevisaoExecucao.query.filter_by(ativa=True).one().id


def test_agente_sem_selecao_nao_treina_todo_historico_implicitamente(configured):
    service, paths, _ = configured
    service.carregar(paths["manifest"])
    before = set((service.root / "data/artifacts").glob("previsao-*"))
    with pytest.raises(PrevisaoError) as error:
        service.recalcular("2026-01-10", 7)
    assert error.value.code == "SELECTION_EMPTY"
    assert set((service.root / "data/artifacts").glob("previsao-*")) == before
    assert active_id(service) == json.loads(paths["manifest"].read_text())["run_id"]


def test_cli_exporta_snapshot_privado_sem_imprimir_codigos(experimental_configured):
    service, _, _ = experimental_configured
    result = service.app.test_cli_runner().invoke(args=["previsao", "exportar-selecao"])
    assert result.exit_code == 0
    assert "00110" not in result.output
    paths = list((service.root / "data/reports").glob("selecao-experimental-*.json"))
    assert len(paths) == 1 and paths[0].stat().st_mode & 0o777 == 0o600
    with service.app.app_context():
        assert json.loads(paths[0].read_text()) == catalog.snapshot_selecao_experimental(db.session)


def test_nova_execucao_experimental_treina_somente_codigo_selecionado_e_preserva_publicacoes_anteriores(experimental_configured):
    service, paths, (product, _, _) = experimental_configured
    result = json.loads(paths["forecast"].read_text())
    report = json.loads(paths["report"].read_text())
    manifest = json.loads(paths["manifest"].read_text())
    assert set(result["forecast"]["products"]) == set(result["models"]) == {"00110"}
    assert set(report["evaluations"]) == set(report["dataset"]["products"]) == {"00110"}
    assert report["dataset"]["requested_codes"] == ["00110"]
    assert report["dataset"]["excluded_not_selected"] > 0
    assert result["experimento"] == report["experimento"] == manifest["experimento"]
    general = service.consultar()
    selected = service.consultar(product)
    assert general["total_previsto"] == selected["total_previsto"]
    assert general["periodos"] == selected["periodos"]
    assert set(general["modelo"]) == {"00110"}
    with service.app.app_context():
        executions = PrevisaoExecucao.query.all()
        assert len(executions) == 2
        previous = next(run for run in executions if not run.ativa)
        previous_manifest = service.root / previous.manifest_path
        assert previous_manifest.exists() and previous_manifest != paths["manifest"]
        old = json.loads(previous_manifest.read_text())
        old_forecast = json.loads((service.root / old["forecast_file"]).read_text())
        assert set(old_forecast["models"]) == {"00110", "00220"}
        assert "experimento" not in old_forecast


def test_codigo_nao_associado_e_codigo_excluido_nao_representam_previsao_zero(experimental_configured):
    service, _, ids = experimental_configured
    with pytest.raises(PrevisaoError) as error:
        service.consultar(ids[1])
    assert error.value.code == "PRODUCT_UNMAPPED"
    with pytest.raises(PrevisaoError) as error:
        service.painel(codigo_produto="00220")
    assert error.value.code == "HISTORY_NOT_FOUND"
    assert [row["codigo_produto"] for row in service.painel()["produtos"]] == ["00110"]


def test_desmarcar_associacao_exige_nova_execucao_sem_reescrever_publicacao(experimental_configured):
    service, paths, (product, _, _) = experimental_configured
    before = {path: path.read_bytes() for path in paths.values()}
    current = active_id(service)
    deselect(service, product)
    with pytest.raises(PrevisaoError) as error:
        service.consultar()
    assert error.value.code == "SELECTION_CHANGED"
    with pytest.raises(PrevisaoError) as error:
        service.consultar(product)
    assert error.value.code == "PRODUCT_NOT_SELECTED"
    assert active_id(service) == current
    assert {path: path.read_bytes() for path in paths.values()} == before


def test_mudanca_da_selecao_apos_treinar_impede_publicacao_e_preserva_execucao_ativa(experimental_configured, monkeypatch):
    service, _, (product, _, _) = experimental_configured
    current = active_id(service)
    original = previsao_service.run_forecast

    def change_after_training(*args, **kwargs):
        paths = original(*args, **kwargs)
        deselect(service, product)
        return paths

    monkeypatch.setattr(previsao_service, "run_forecast", change_after_training)
    with pytest.raises(PrevisaoError) as error:
        service.recalcular("2026-01-10", 7)
    assert error.value.code == "SELECTION_CHANGED"
    assert active_id(service) == current
    with service.app.app_context():
        assert PrevisaoExecucao.query.count() == 2


@pytest.fixture
def experimental_timed(experimental_configured, monkeypatch):
    service, _, ids = experimental_configured
    original = preventive._hoje
    monkeypatch.setattr(preventive, "_hoje", lambda value: original(date(2026, 1, 11) if value is None else value))
    monkeypatch.setattr(previsao_service, "hoje_manaus", lambda: date(2026, 1, 11))
    monkeypatch.setattr(estoque_agent, "datetime", SimpleNamespace(now=lambda zone: datetime(2026, 1, 11, tzinfo=zone)))
    with service.app.app_context():
        product = db.session.get(Produto, ids[0])
        product.quantidade_disponivel, product.estoque_minimo = 5, 3
        db.session.commit()
    return experimental_configured


def test_http_e_xmpp_consultam_codigo_exato_e_calculam_alerta_com_saldo_simulado(experimental_timed, runtime):
    service, paths, (product, _, _) = experimental_timed
    gateway = AgentGateway(runtime)
    runtime.call(lambda: runtime.agents["previsao"].aguardar_inicializacao())
    client = service.app.test_client()
    response = client.get("/api/previsoes")
    assert response.status_code == 200
    assert response.json["experimento"]["origem_historico"] == "real"
    assert response.json["experimento"]["estoque"] == "simulado"
    assert [row["codigo_produto"] for row in response.json["produtos"]] == ["00110"]
    forecast = gateway.consultar_previsao(product).payload
    assert forecast["codigo_produto"] == "00110" and forecast["produto_id"] == product
    assert forecast["experimento"]["vendas_operacionais"] == "simuladas"
    result = gateway.sincronizar_alerta_preventivo(product)
    assert result["status"] == "ok" and result["risco"] is True
    assert Decimal(result["saldo_projetado"]) == Decimal(5) - Decimal(forecast["total_previsto"])
    assert result["disponivel"] == 5
    with service.app.app_context():
        assert db.session.get(Produto, product).quantidade_disponivel == 5
        assert AlertaEstoque.query.filter_by(tipo_alerta="risco_futuro", ativo=True).count() == 1
    current = active_id(service)
    deselect(service, product)
    conflict = client.get("/api/previsoes")
    assert conflict.status_code == 409 and conflict.json["code"] == "SELECTION_CHANGED"
    assert "periodos" not in conflict.json and active_id(service) == current


@pytest.mark.parametrize("existing", [False, True])
def test_desmarcar_durante_rpc_nao_cria_nem_resolve_alerta_experimental(experimental_timed, runtime, monkeypatch, existing):
    service, _, (product, _, _) = experimental_timed
    gateway = AgentGateway(runtime)
    if existing:
        gateway.sincronizar_alerta_preventivo(product)
        with service.app.app_context():
            alert = AlertaEstoque.query.filter_by(tipo_alerta="risco_futuro", ativo=True).one()
            snapshot = deepcopy(alert.previsao_json)
            db.session.get(Produto, product).quantidade_disponivel = 100
            db.session.commit()
    original = runtime.agents["previsao"].service.consultar

    def deselect_after_query(**kwargs):
        result = original(**kwargs)
        deselect(service, product)
        return result

    monkeypatch.setattr(runtime.agents["previsao"].service, "consultar", deselect_after_query)
    with pytest.raises(EstoqueError) as error:
        gateway.sincronizar_alerta_preventivo(product)
    assert error.value.code in {"SELECTION_CHANGED", "PRODUCT_NOT_SELECTED"}
    with service.app.app_context():
        alerts = AlertaEstoque.query.filter_by(tipo_alerta="risco_futuro").all()
        if existing:
            assert len(alerts) == 1 and alerts[0].ativo and alerts[0].previsao_json == snapshot
        else:
            assert alerts == []
