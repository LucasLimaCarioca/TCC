"""Interface HTTP com histórico, modelos e códigos exclusivamente fictícios."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import logging
from threading import Event, get_ident
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.agents.base_agent import AgentUnavailable, RemoteFailure
from app.agents.protocol import JIDS, PREVISAO_PAINEL
from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.log_mensagem_agente import LogMensagemAgente
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.previsao_demanda import PrevisaoDemanda
from app.models.previsao_execucao import PrevisaoExecucao
from app.models.produto import Produto
from app.models.venda import Venda
from app.models.venda_historica import VendaHistorica
from app.routes import previsao_routes
from app.services import previsao_service
from app.services.previsao_service import PrevisaoError
from test_agent_runtime import runtime
from test_forecasting_service import project
from test_previsao_service import configured


class PanelGateway:
    """Mantém o domínio real nas consultas e controla somente o transporte HTTP."""

    def __init__(self, service=None, error=None):
        self.service = service
        self.error = error
        self.calls = []
        self.job_id = str(uuid4())
        self.status = "em_execucao"

    def _check(self):
        if self.error is not None:
            raise self.error

    def consultar_painel_previsao(self, **payload):
        self.calls.append(("painel", payload))
        self._check()
        try:
            return SimpleNamespace(payload=self.service.painel(**payload))
        except PrevisaoError as error:
            raise RemoteFailure({"code": error.code, "message": str(error)}) from None

    def atualizar_previsao(self, **payload):
        self.calls.append(("atualizar", payload))
        self._check()
        return SimpleNamespace(payload={"job_id": self.job_id, "status": "em_execucao"})

    def status_previsao(self, job_id):
        self.calls.append(("status", job_id))
        self._check()
        return SimpleNamespace(payload={"job_id": job_id, "status": self.status,
            "execucao_id": None, "erro": None if self.status == "em_execucao" else
            {"code": "UPDATE_FAILED", "message": "Falha fictícia na atualização."}})


@pytest.fixture
def panel_http(configured):
    service, paths, ids = configured
    service.carregar(paths["manifest"])
    gateway = PanelGateway(service)
    service.app.extensions["agent_gateway"] = gateway
    return service.app.test_client(), gateway, service, paths, ids


def _no_store(response):
    assert response.headers["Cache-Control"] == "no-store"


def _state(app):
    models = (Produto, AlertaEstoque, Venda, VendaHistorica, MovimentacaoEstoque,
              PrevisaoDemanda, PrevisaoExecucao)
    with app.app_context():
        return {model.__tablename__: deepcopy(db.session.execute(
            model.__table__.select().order_by(*model.__table__.primary_key)
        ).all()) for model in models}


@pytest.mark.parametrize("frequency", ["diaria", "semanal", "mensal"])
@pytest.mark.parametrize("code", [None, "00110", "00220"])
def test_get_delega_filtros_preserva_codigo_e_exibe_dominio_real(panel_http, frequency, code):
    client, gateway, service, _, _ = panel_http
    query = {"granularidade": frequency, "data_inicio": "2026-01-12", "data_fim": "2026-01-14"}
    if code is not None:
        query["codigo_produto"] = code
    response = client.get("/api/previsoes", query_string=query)
    assert response.status_code == 200
    _no_store(response)
    expected = {"codigo_produto": code, **{key: query[key] for key in
                ("granularidade", "data_inicio", "data_fim")}}
    assert gateway.calls == [("painel", expected)]
    result = response.get_json()
    assert result == service.painel(**expected)
    assert result["selecao"]["codigo_produto"] == code
    assert sum(Decimal(period["quantidade_prevista"]) for period in result["periodos"]) == Decimal(result["total_previsto"])
    assert all(isinstance(period["quantidade_prevista"], str) for period in result["periodos"])
    assert len(result["avaliacoes"]) == (2 if code is None else 1)
    if code == "00220":
        product = next(item for item in result["produtos"] if item["codigo_produto"] == code)
        assert product["produto_id"] is product["nome"] is None


def test_get_padrao_usa_cobertura_publicada_sem_impor_hoje(panel_http):
    client, gateway, _, _, _ = panel_http
    response = client.get("/api/previsoes")
    assert response.status_code == 200
    _no_store(response)
    assert gateway.calls == [("painel", {"codigo_produto": None, "granularidade": "diaria",
                                        "data_inicio": None, "data_fim": None})]
    result = response.get_json()
    assert result["selecao"]["data_inicio"] == result["cobertura"]["inicio"] == "2026-01-11"
    assert result["selecao"]["data_fim"] == result["cobertura"]["fim"] == "2026-01-20"


@pytest.mark.parametrize("query", [
    "campo_desconhecido=ficticio", "horizonte_dias=7", "produto_id=1",
    "codigo_produto=00110&codigo_produto=00220", "granularidade=diaria&granularidade=mensal",
    "data_inicio=2026-01-11&data_inicio=2026-01-12", "data_fim=2026-01-14&data_fim=2026-01-15",
    "granularidade=anual", "codigo_produto=%20FICTICIO", "codigo_produto=FICTICIO%0A",
    "data_inicio=2026-1-11", "data_inicio=20260111", "data_inicio=2026-02-30",
    "data_fim=nao-e-data", "data_inicio=2026-01-14&data_fim=2026-01-12",
    "data_inicio=2025-01-01&data_fim=2026-01-02",
])
def test_get_filtro_invalido_nao_chama_gateway(app, query):
    gateway = PanelGateway(error=AssertionError("Não deve consultar com parâmetros inválidos."))
    app.extensions["agent_gateway"] = gateway
    response = app.test_client().get("/api/previsoes?" + query)
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_INPUT"
    _no_store(response)
    assert gateway.calls == []


@pytest.mark.parametrize("query,code,status", [
    ({"data_inicio": "2026-01-10"}, "HORIZON_UNAVAILABLE", 400),
    ({"data_fim": "2026-01-21"}, "HORIZON_UNAVAILABLE", 400),
    ({"codigo_produto": "FICTICIO-INEXISTENTE"}, "HISTORY_NOT_FOUND", 404),
])
def test_get_intervalo_ou_codigo_sem_cobertura_falha_sem_zerofill(panel_http, query, code, status):
    client, _, _, _, _ = panel_http
    response = client.get("/api/previsoes", query_string=query)
    assert response.status_code == status
    assert response.get_json()["code"] == code
    assert "periodos" not in response.get_json()
    _no_store(response)


def test_get_tela_e_consultas_nao_modificam_alertas_estoque_historico_ou_artefatos(panel_http):
    client, _, service, _, ids = panel_http
    with service.app.app_context():
        db.session.add(AlertaEstoque(tipo_item="produto", produto_id=ids[0], tipo_alerta="risco_futuro",
                                    nivel="aviso", mensagem="Alerta fictício a preservar", ativo=True))
        db.session.commit()
    before = _state(service.app)
    files = {path: path.read_bytes() for path in (service.root / "data").rglob("*") if path.is_file()}
    for path in ("/previsoes", "/api/previsoes", "/api/previsoes?codigo_produto=00220&granularidade=mensal"):
        response = client.get(path)
        assert response.status_code == 200
        _no_store(response)
    assert _state(service.app) == before
    assert {path: path.read_bytes() for path in files} == files
    assert set(files) == {path for path in (service.root / "data").rglob("*") if path.is_file()}


def test_tela_sem_runtime_renderiza_navegacao_e_recursos_locais_sem_consultar_gateway(app):
    app.extensions["agent_gateway"] = PanelGateway(error=AssertionError("A estrutura não consulta modelos."))
    response = app.test_client().get("/previsoes")
    assert response.status_code == 200
    _no_store(response)
    text = response.get_data(as_text=True)
    for expected in ("Atendimento", "Estoque", "Previsões", "Vendas", "previsao-grafico",
                     "previsao-metricas", "/static/js/previsoes.js"):
        assert expected in text
    assert "https://" not in text and "http://" not in text
    assert app.extensions["agent_gateway"].calls == []


@pytest.mark.parametrize("path,method", [
    ("/api/previsoes", "get"), ("/api/previsoes/atualizar", "post"),
    ("/api/previsoes/tarefas/00000000-0000-4000-8000-000000000001", "get"),
])
def test_api_sem_runtime_informa_indisponibilidade(app, path, method):
    app.extensions.pop("agent_gateway", None)
    kwargs = {"json": {"data_corte": "2026-01-10", "horizonte_dias": 28}} if method == "post" else {}
    response = getattr(app.test_client(), method)(path, **kwargs)
    assert response.status_code == 503
    assert response.get_json()["code"] == "FORECAST_UNAVAILABLE"
    _no_store(response)


def test_sem_modelo_ativo_retorna_indisponibilidade_sem_resultado_inventado(configured):
    service, _, _ = configured
    service.app.extensions["agent_gateway"] = PanelGateway(service)
    response = service.app.test_client().get("/api/previsoes")
    assert response.status_code == 503
    assert response.get_json()["code"] == "MODEL_UNAVAILABLE"
    assert "total_previsto" not in response.get_json()
    _no_store(response)


def test_post_retorna_202_com_tarefa_sem_treinar_na_rota(app, monkeypatch):
    gateway = PanelGateway()
    app.extensions["agent_gateway"] = gateway
    def forbidden(*args, **kwargs):
        pytest.fail("A rota não pode treinar o motor diretamente.")
    monkeypatch.setattr(previsao_service, "run_forecast", forbidden)
    payload = {"data_corte": "2026-01-10", "horizonte_dias": 28}
    response = app.test_client().post("/api/previsoes/atualizar", json=payload)
    assert response.status_code == 202
    assert response.get_json() == {"job_id": gateway.job_id, "status": "em_execucao"}
    assert str(UUID(response.get_json()["job_id"])) == gateway.job_id
    assert gateway.calls == [("atualizar", payload)]
    _no_store(response)


@pytest.mark.parametrize("payload", [
    None, [], "texto", {}, {"data_corte": "2026-01-10"},
    {"data_corte": "2026-01-10", "horizonte_dias": 28, "codigo_produto": "00110"},
    {"data_corte": "2026-1-10", "horizonte_dias": 28},
    {"data_corte": "2026-02-30", "horizonte_dias": 28},
    {"data_corte": "2026-01-10", "horizonte_dias": True},
    {"data_corte": "2026-01-10", "horizonte_dias": "28"},
    {"data_corte": "2026-01-10", "horizonte_dias": 1.5},
    {"data_corte": "2026-01-10", "horizonte_dias": 0},
    {"data_corte": "2026-01-10", "horizonte_dias": 367},
])
def test_post_payload_invalido_nao_agenda_trabalho(app, payload):
    gateway = PanelGateway(error=AssertionError("Payload inválido não deve ser agendado."))
    app.extensions["agent_gateway"] = gateway
    response = app.test_client().post("/api/previsoes/atualizar", json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_INPUT"
    assert gateway.calls == []
    _no_store(response)


@pytest.mark.parametrize("content,content_type", [("{", "application/json"),
    ('{"data_corte":"2026-01-10","horizonte_dias":28}', "text/plain")])
def test_post_exige_json_valido(app, content, content_type):
    gateway = PanelGateway()
    app.extensions["agent_gateway"] = gateway
    response = app.test_client().post("/api/previsoes/atualizar", data=content, content_type=content_type)
    assert response.status_code == 400
    assert gateway.calls == []
    _no_store(response)


@pytest.mark.parametrize("job", ["invalido", "00000000000040008000000000000001",
    "00000000-0000-4000-8000-00000000000A"])
def test_status_exige_uuid_canonico_sem_chamar_gateway(app, job):
    gateway = PanelGateway()
    app.extensions["agent_gateway"] = gateway
    response = app.test_client().get("/api/previsoes/tarefas/" + job)
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_INPUT"
    assert gateway.calls == []
    _no_store(response)


def test_status_preserva_falha_do_job_sem_sinalizar_sucesso(app):
    gateway = PanelGateway()
    gateway.status = "falhou"
    app.extensions["agent_gateway"] = gateway
    response = app.test_client().get("/api/previsoes/tarefas/" + gateway.job_id)
    assert response.status_code == 200
    assert response.get_json()["status"] == "falhou"
    assert response.get_json()["erro"]["code"] == "UPDATE_FAILED"
    assert gateway.calls == [("status", gateway.job_id)]
    _no_store(response)


@pytest.mark.parametrize("code,status", [("HORIZON_UNAVAILABLE", 400), ("MODEL_UNAVAILABLE", 503),
    ("JOB_NOT_FOUND", 404), ("FORECAST_BUSY", 409), ("INVALID_ARTIFACT", 503)])
def test_falha_remota_e_traduzida_para_estado_http_tratado(app, code, status):
    app.extensions["agent_gateway"] = PanelGateway(error=RemoteFailure({"code": code, "message": "Falha fictícia tratada."}))
    response = app.test_client().get("/api/previsoes")
    assert response.status_code == status
    assert response.get_json() == {"code": code, "erro": "Falha fictícia tratada."}
    _no_store(response)


@pytest.mark.parametrize("error", [AgentUnavailable("DETALHE_FICTICIO_RESTRITO"),
                                  RuntimeError("DETALHE_FICTICIO_RESTRITO")])
def test_erro_interno_e_sanitizado_na_resposta_e_logs(app, caplog, error):
    app.extensions["agent_gateway"] = PanelGateway(error=error)
    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/api/previsoes")
    assert response.status_code == 503
    assert "DETALHE_FICTICIO_RESTRITO" not in response.get_data(as_text=True)
    assert "DETALHE_FICTICIO_RESTRITO" not in caplog.text
    _no_store(response)


@pytest.mark.parametrize("suffix", ["?codigo_produto=CODIGO_FICTICIO_PRIVADO&granularidade=mensal",
    "?granularidade=diaria&codigo_produto=CODIGO_FICTICIO_PRIVADO",
    "/tarefas/00000000-0000-4000-8000-000000000001?codigo_produto=CODIGO_FICTICIO_PRIVADO"])
def test_access_log_redige_filtros_historicos(app, caplog, suffix):
    logger = logging.getLogger("werkzeug")
    with caplog.at_level(logging.INFO, logger="werkzeug"):
        logger.info('%s - - "%s %s HTTP/1.1" %s -', "127.0.0.1", "GET", "/api/previsoes" + suffix, 200)
    assert "CODIGO_FICTICIO_PRIVADO" not in caplog.text
    assert "?redacted" in caplog.text
    assert "/api/previsoes" in caplog.text


def test_access_log_instala_protecao_sem_duplicar(app):
    previsao_routes.protect_forecast_access_logs()
    previsao_routes.protect_forecast_access_logs()
    assert sum(isinstance(item, previsao_routes._PrivateForecastAccess)
               for item in logging.getLogger("werkzeug").filters) == 1


def test_http_xmpp_real_consulta_e_atualiza_sem_bloquear_publicacao(configured, runtime, monkeypatch):
    service, paths, _ = configured
    service.carregar(paths["manifest"])
    runtime.call(lambda: runtime.agents["previsao"].aguardar_inicializacao())
    client = service.app.test_client()
    before = client.get("/api/previsoes", query_string={"codigo_produto": "00220", "granularidade": "semanal"})
    assert before.status_code == 200
    assert before.get_json()["selecao"]["codigo_produto"] == "00220"
    assert next(item for item in before.get_json()["produtos"] if item["codigo_produto"] == "00220")["produto_id"] is None
    # A publicação legada continua consultável por código. A nova atualização
    # parte de uma seleção experimental explicitamente confirmada e publicada.
    from experimental_fixtures import prepare_experimental_configured
    service, paths, _ = prepare_experimental_configured(configured)
    before = client.get("/api/previsoes")
    assert before.status_code == 200 and before.get_json()["experimento"]["estoque"] == "simulado"
    old_run = before.get_json()["execucao_id"]
    entered, release = Event(), Event()
    worker_threads = []
    worker_service = runtime.agents["previsao"].service
    original = worker_service.recalcular
    def blocked(**kwargs):
        worker_threads.append(get_ident())
        entered.set()
        if not release.wait(5):
            raise AssertionError("O teste precisa liberar o trabalhador fictício.")
        return original(**kwargs)
    monkeypatch.setattr(worker_service, "recalcular", blocked)
    monkeypatch.setattr(previsao_service, "hoje_manaus", lambda: date(2026, 1, 11))
    try:
        accepted = client.post("/api/previsoes/atualizar", json={"data_corte": "2026-01-10", "horizonte_dias": 5})
        assert accepted.status_code == 202
        _no_store(accepted)
        job_id = accepted.get_json()["job_id"]
        assert entered.wait(2)
        assert worker_threads == [worker_threads[0]] and worker_threads[0] != get_ident()
        status = client.get("/api/previsoes/tarefas/" + job_id)
        assert status.get_json()["status"] == "em_execucao"
        _no_store(status)
        while_running = client.get("/api/previsoes")
        assert while_running.status_code == 200
        assert while_running.get_json()["execucao_id"] == old_run
    finally:
        release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get("/api/previsoes/tarefas/" + job_id)
        if status.get_json()["status"] != "em_execucao":
            break
        time.sleep(0.05)
    assert status.get_json()["status"] == "concluido", status.get_json()
    published = client.get("/api/previsoes")
    assert published.status_code == 200
    assert published.get_json()["execucao_id"] == status.get_json()["execucao_id"] != old_run
    assert published.get_json()["cobertura"]["fim"] == "2026-01-15"
    with service.app.app_context():
        logs = LogMensagemAgente.query.filter_by(ontology=PREVISAO_PAINEL).all()
        assert {(log.sender, log.performative, log.status) for log in logs} >= {
            (JIDS["estoque"], "request", "sent"), (JIDS["previsao"], "inform", "received")}
        assert all(log.payload == {"redacted": True} for log in logs)
