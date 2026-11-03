"""Sino usa contagens persistidas, com banco e previsões fictícios."""

from datetime import timedelta

import pytest
from sqlalchemy import event

from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.catalogacao_historica import RevisaoAssociacaoHistorica
from app.models.previsao_execucao import PrevisaoExecucao
from app.models.produto import Produto
from app.models.venda_historica import VendaHistorica
from app.services import alerta_preventivo_service as preventive
from app.services import catalogacao_service as catalog
from app.services import estoque_service as stock
from app.services.previsao_service import PrevisaoService
from experimental_fixtures import experimental_configured
from test_agent_runtime import runtime
from test_alerta_preventivo_service import TODAY, ready, sync
from test_forecasting_service import project
from test_previsao_service import configured


def test_resumo_conta_ativos_sem_montar_visao_completa(client, catalogo, monkeypatch):
    product = catalogo["caixa de 10L - chocolate"]
    client.put("/api/estoque/minimo", json={"tipo_item": "produto", "item_id": product, "estoque_minimo": 20})
    monkeypatch.setattr(stock, "visao_estoque", lambda: pytest.fail("Sino não deve montar estoque."))
    response = client.get("/api/estoque/alertas/resumo")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json == {"total": 1, "immediate": 1, "preventive": 0, "pending": 0}
    client.put("/api/estoque/minimo", json={"tipo_item": "produto", "item_id": product, "estoque_minimo": 0})
    assert client.get("/api/estoque/alertas/resumo").json == {
        "total": 0, "immediate": 0, "preventive": 0, "pending": 0}


def test_resumo_spade_usa_autoridade_do_estoque(app, runtime, monkeypatch):
    calls = []
    original = runtime.agents["estoque"].executar

    async def observe(command, **kwargs):
        calls.append(command)
        return await original(command, **kwargs)

    monkeypatch.setattr(runtime.agents["estoque"], "executar", observe)
    response = app.test_client().get("/api/estoque/alertas/resumo")
    assert response.status_code == 200
    assert response.json == {"total": 0, "immediate": 0, "preventive": 0, "pending": 0}
    assert calls == ["resumo_alertas"]


@pytest.fixture
def alert_ready(ready, monkeypatch):
    sync(ready)
    original = preventive._hoje
    monkeypatch.setattr(preventive, "_hoje", lambda value: original(TODAY if value is None else value))
    return ready


def test_resumo_nao_abre_artefatos_nem_movimentos_e_nao_escreve(alert_ready, monkeypatch):
    service, _, _ = alert_ready
    monkeypatch.setattr(PrevisaoService, "_bundle", lambda *args, **kwargs: pytest.fail("Não abrir artefatos."))
    statements = []
    with service.app.app_context():
        before = AlertaEstoque.query.one().previsao_json

        def track(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())

        event.listen(db.engine, "before_cursor_execute", track)
        try:
            result = stock.resumo_alertas()
        finally:
            event.remove(db.engine, "before_cursor_execute", track)
        assert result == {"total": 1, "immediate": 0, "preventive": 1, "pending": 0}
        assert AlertaEstoque.query.one().previsao_json == before
    assert all(statement.lstrip().startswith("select") for statement in statements)
    assert not any("movimentacoes_estoque" in statement for statement in statements)
    assert sum("vendas_historicas" in statement for statement in statements) == 1


@pytest.mark.parametrize("change", ["saldo", "minimo", "dia", "horizonte", "vinculo", "identidade", "publicacao", "historico"])
def test_resumo_detecta_mudancas_sem_reutilizar_estado_invalido(alert_ready, monkeypatch, change):
    service, product_id, _ = alert_ready
    with service.app.app_context():
        assert stock.resumo_alertas()["pending"] == 0
        product = db.session.get(Produto, product_id)
        if change == "saldo":
            product.quantidade_disponivel += 1
        elif change == "minimo":
            product.estoque_minimo += 1
        elif change == "dia":
            monkeypatch.setattr(preventive, "_hoje", lambda value: TODAY + timedelta(days=1))
        elif change == "horizonte":
            service.app.config["PREDICTIVE_ALERT_HORIZON_DAYS"] = 8
        elif change == "vinculo":
            db.session.delete(db.session.get(RevisaoAssociacaoHistorica, product_id))
        elif change == "identidade":
            product.nome = "Nova identidade fictícia"
        elif change == "publicacao":
            PrevisaoExecucao.query.one().ativa = False
        else:
            db.session.add(VendaHistorica(codigo_produto="00110", data=TODAY,
                codigo_grupo="NOVO-FICTICIO", categoria="Categoria fictícia alterada",
                quantidade=1, numero_pedidos=1, source_hash="a" * 64, dataset_hash="b" * 64))
        db.session.commit()
        assert stock.resumo_alertas() == {"total": 1, "immediate": 0, "preventive": 1, "pending": 1}
        assert AlertaEstoque.query.one().ativo


def test_visao_completa_reutiliza_metadados_uma_vez_e_ignora_alertas_resolvidos(alert_ready, monkeypatch):
    service, product_id, _ = alert_ready
    original, calls = catalog._historico, []

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog, "_historico", tracked)
    with service.app.app_context():
        db.session.add(AlertaEstoque(tipo_item="produto", produto_id=product_id,
            tipo_alerta="risco_futuro", mensagem="Resolvido fictício", nivel="aviso", ativo=False))
        db.session.commit()
        state = stock.visao_estoque()
        assert len(state["alertas"]) == 2
        assert len(calls) == 1
        assert next(item for item in state["alertas"] if not item["ativo"])["avaliacao"]["motivo"] == "resolvido"


def test_snapshot_comercial_ausente_na_partida_mantem_pendencia_conservadora(alert_ready, monkeypatch):
    service, _, _ = alert_ready
    service.app.extensions.pop("forecast_catalog_metadata", None)
    monkeypatch.setattr(PrevisaoService, "_bundle", lambda *args, **kwargs: pytest.fail("Não abrir artefatos."))
    with service.app.app_context():
        assert stock.resumo_alertas()["pending"] == 1


def test_resumo_preserva_validacao_da_selecao_experimental(experimental_configured, monkeypatch):
    service, _, ids = experimental_configured
    product_id = ids[0]
    with service.app.app_context():
        product = db.session.get(Produto, product_id)
        product.quantidade_disponivel, product.estoque_minimo = 0, 3
        db.session.commit()
    payload = service.consultar(product_id)
    with service.app.app_context():
        preventive.sincronizar_alerta_preventivo(product_id, payload, hoje=TODAY)
        monkeypatch.setattr(preventive, "_hoje", lambda value: TODAY)
        assert stock.resumo_alertas()["pending"] == 0
        db.session.get(RevisaoAssociacaoHistorica, product_id).selecionado_previsao = False
        db.session.commit()
        assert stock.resumo_alertas()["pending"] == 1
