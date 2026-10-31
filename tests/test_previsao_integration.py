"""Motor, persistência e transporte XMPP juntos, somente com fontes fictícias."""

from datetime import date
import json
import time

from app.agents.gateway import AgentGateway
from app.database import db
from app.models.previsao_demanda import PrevisaoDemanda
from app.services import previsao_service
from test_agent_runtime import runtime, free_ports
from test_previsao_service import configured
from test_forecasting_service import project
from experimental_fixtures import experimental_configured


def test_modelo_publicado_responde_por_xmpp_e_recalcula_sem_duplicar(experimental_configured, runtime, monkeypatch):
    service, paths, (product, _, _) = experimental_configured
    service.carregar(paths["manifest"])
    gateway = AgentGateway(runtime)
    runtime.call(lambda: runtime.agents["previsao"].aguardar_inicializacao())
    forecast = gateway.consultar_previsao(product, "semanal", 3)
    assert forecast.payload == service.consultar(product, "semanal", 3)
    with service.app.app_context():
        count = PrevisaoDemanda.query.count()
    general = gateway.consultar_previsao(None, "mensal", 3)
    assert general.payload == service.consultar(None, "mensal", 3)
    with service.app.app_context():
        assert PrevisaoDemanda.query.count() == count
    monkeypatch.setattr(previsao_service, "hoje_manaus", lambda: date(2026, 1, 11))
    job = gateway.atualizar_previsao("2026-01-10", 5).payload
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = gateway.status_previsao(job["job_id"]).payload
        if status["status"] != "em_execucao":
            break
        time.sleep(0.05)
    assert status["status"] == "concluido", status
    assert status["execucao_id"] != forecast.payload["execucao_id"]
    assert gateway.consultar_previsao(product, "diaria", 5).payload["execucao_id"] == status["execucao_id"]


def test_cli_consulta_xmpp_grava_somente_saida_privada_e_encerra_runtime(configured):
    service, paths, (product, _, _) = configured
    service.carregar(paths["manifest"])
    first, second = free_ports()
    app = service.app
    app.config.update(XMPP_CLIENT_PORT=first, XMPP_SERVER_PORT=second,
        XMPP_ATENDIMENTO_PASSWORD="senha-ficticia", XMPP_ESTOQUE_PASSWORD="senha-ficticia",
        XMPP_PREVISAO_PASSWORD="senha-ficticia")
    response = app.test_cli_runner().invoke(args=["previsao", "consultar", "--produto-id", str(product),
                                                "--granularidade", "mensal", "--horizonte", "3"])
    assert response.exit_code == 0, response.output
    assert "00110" not in response.output
    generated = list((service.root / "data/reports").glob("consulta-agente-*.json"))
    assert len(generated) == 1
    assert generated[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(generated[0].read_text())["codigo_produto"] == "00110"
    assert not app.extensions["agent_runtime"].thread.is_alive()
    assert not app.extensions["agent_runtime"].server.is_alive()
