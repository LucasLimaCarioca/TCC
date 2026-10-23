"""Alertas persistentes através dos agentes reais, com histórico fictício."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
import json
import time
from threading import Event

import pytest

from app.agents.base_agent import RemoteFailure
from app.agents.gateway import AgentGateway
from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.log_mensagem_agente import LogMensagemAgente
from app.models.produto import Produto
from app.models.venda import Venda
from app.services import alerta_preventivo_service as preventive
from app.services import previsao_service
from test_agent_runtime import runtime
from test_alerta_preventivo_service import ready
from test_previsao_service import configured
from test_forecasting_service import project


@pytest.fixture
def automatic_ready(ready, monkeypatch):
    service, product, payload = ready
    service.app.config.update(PREDICTIVE_ALERTS_AUTO_ENABLED=True, PREDICTIVE_ALERT_HORIZON_DAYS=7)
    original = preventive._hoje
    monkeypatch.setattr(preventive, "_hoje", lambda value: original(date(2026, 1, 11) if value is None else value))
    monkeypatch.setattr(previsao_service, "hoje_manaus", lambda: date(2026, 1, 11))
    return ready


def wait_alerts(runtime):
    async def wait():
        if runtime._predictive_startup_task is not None:
            await asyncio.shield(runtime._predictive_startup_task)
        await runtime.agents["estoque"].aguardar_alertas()
    runtime.call(wait, timeout=15)


def alert_rows(service):
    with service.app.app_context():
        return [(a.id, a.ativo, a.previsao_json, a.resolvido_em) for a in
                AlertaEstoque.query.filter_by(tipo_alerta="risco_futuro").order_by(AlertaEstoque.id)]


def test_inicializacao_e_repeticao_criam_um_alerta_por_xmpp(automatic_ready, runtime):
    service, product, _ = automatic_ready
    wait_alerts(runtime)
    first = alert_rows(service)
    assert len(first) == 1 and first[0][1]
    result = AgentGateway(runtime).sincronizar_alerta_preventivo(product)
    assert result["acao"] == "atualizado" and result["alerta_id"] == first[0][0]
    assert len(alert_rows(service)) == 1
    with service.app.app_context():
        assert LogMensagemAgente.query.filter_by(ontology="previsao.consulta").count() > 0
        assert all(log.payload == {"redacted": True} for log in LogMensagemAgente.query.all())


def test_reposicao_resolve_e_venda_reabre_sem_alterar_confirmacao(automatic_ready, runtime):
    service, product, _ = automatic_ready
    wait_alerts(runtime)
    gateway = AgentGateway(runtime)
    gateway.executar_estoque("movimentar", tipo_item="produto", item_id=product,
                            tipo_movimentacao="entrada", quantidade=20, motivo="Reposição fictícia")
    wait_alerts(runtime)
    assert not alert_rows(service)[0][1] and alert_rows(service)[0][3] is not None
    reply = gateway.baixar_estoque([{"produto_id": product, "quantidade": 20}], "Cliente Fictício")
    assert reply.payload["success"] is True
    wait_alerts(runtime)
    rows = alert_rows(service)
    assert len(rows) == 2 and not rows[0][1] and rows[1][1]
    with service.app.app_context():
        assert Venda.query.count() == 1
        assert db.session.get(Produto, product).quantidade_disponivel == 5


def test_mudanca_minimo_e_horizonte_reavaliam_mesmo_alerta(automatic_ready, runtime):
    service, product, _ = automatic_ready
    wait_alerts(runtime)
    gateway = AgentGateway(runtime)
    gateway.executar_estoque("movimentar", tipo_item="produto", item_id=product,
                            tipo_movimentacao="ajuste", quantidade=13, motivo="Contagem fictícia")
    wait_alerts(runtime)
    assert not alert_rows(service)[-1][1]  # projetado igual ao mínimo não é risco
    gateway.executar_estoque("configurar_minimo", tipo_item="produto", item_id=product, estoque_minimo=4)
    wait_alerts(runtime)
    assert alert_rows(service)[-1][1]
    result = gateway.sincronizar_alerta_preventivo(product, 3)
    assert not result["risco"] and result["acao"] == "resolvido"
    assert not alert_rows(service)[-1][1]


def test_falha_de_previsao_preserva_alerta_e_venda_confirmada(automatic_ready, runtime, monkeypatch):
    service, product, _ = automatic_ready
    wait_alerts(runtime)
    original = alert_rows(service)
    def unavailable(**kwargs):
        raise previsao_service.PrevisaoError("INVALID_ARTIFACT", "Artefato fictício inválido.")
    monkeypatch.setattr(runtime.agents["previsao"].service, "consultar", unavailable)
    gateway = AgentGateway(runtime)
    reply = gateway.baixar_estoque([{"produto_id": product, "quantidade": 1}], "Cliente Fictício")
    assert reply.payload["success"]
    wait_alerts(runtime)
    assert alert_rows(service) == original
    result = gateway.sincronizar_alerta_preventivo(product)
    assert result["risco"] is None and result["status"] == "previsao_indisponivel"
    with service.app.app_context():
        from app.services.estoque_service import visao_estoque
        view = visao_estoque()
        alert = next(a for a in view["alertas"] if a["tipo_alerta"] == "risco_futuro")
        assert not alert["avaliacao"]["atual"] and alert["avaliacao"]["motivo"] == "estoque_alterado"


def test_get_risco_e_telas_nao_atualizam_snapshot_preventivo(automatic_ready, runtime):
    service, product, _ = automatic_ready
    wait_alerts(runtime)
    original = alert_rows(service)
    client = service.app.test_client()
    for path in ("/estoque", "/api/estoque/alertas", f"/api/estoque/risco/{product}"):
        assert client.get(path).status_code == 200
    assert alert_rows(service) == original


def test_nova_previsao_notifica_estoque_e_resolve_alerta(automatic_ready, runtime):
    service, product, _ = automatic_ready
    wait_alerts(runtime)
    original = alert_rows(service)[0]
    # Uma nova exportação fictícia aprovada tem demanda observada zero.
    for name in ("canonical.csv", "reviewed.csv"):
        path = service.root / "data/processed" / name
        lines = path.read_text().splitlines()
        revised = [lines[0]]
        for line in lines[1:]:
            fields = line.split(",")
            fields[4] = "0"
            revised.append(",".join(fields))
        path.write_text("\n".join(revised) + "\n")
    digest = sha256((service.root / "data/processed/reviewed.csv").read_bytes()).hexdigest()
    prep = service.root / "data/reports/preparacao-ficticia.json"
    document = json.loads(prep.read_text())
    document["sha256_dataset"] = digest
    prep.write_text(json.dumps(document))
    review = service.root / "data/reports/revisao-ficticia.json"
    document = json.loads(review.read_text())
    document.update(sha256_canonico=digest, sha256_revisado=digest)
    review.write_text(json.dumps(document))
    gateway = AgentGateway(runtime)
    job = gateway.atualizar_previsao("2026-01-10", 7).payload
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = gateway.status_previsao(job["job_id"]).payload
        if status["status"] != "em_execucao":
            break
        time.sleep(0.05)
    assert status["status"] == "concluido", status
    async def notified():
        notifications = tuple(runtime.agents["previsao"]._notification_tasks)
        if notifications:
            await asyncio.gather(*notifications)
    runtime.call(notified, timeout=10)
    wait_alerts(runtime)
    rows = alert_rows(service)
    assert len(rows) == 1 and not rows[0][1]
    assert rows[0][2]["previsao"]["execucao_id"] != original[2]["previsao"]["execucao_id"]
    with service.app.app_context():
        assert LogMensagemAgente.query.filter_by(ontology="previsao.publicada", performative="inform").count() > 0


def test_auto_desabilitado_exige_acao_manual(ready, runtime, monkeypatch):
    service, product, _ = ready
    original = preventive._hoje
    monkeypatch.setattr(preventive, "_hoje", lambda value: original(date(2026, 1, 11) if value is None else value))
    wait_alerts(runtime)
    assert alert_rows(service) == []
    assert AgentGateway(runtime).sincronizar_alerta_preventivo(product)["acao"] == "criado"


def test_worker_encerra_sem_deixar_tarefa_ou_pedido_pendente(automatic_ready, runtime):
    wait_alerts(runtime)
    worker = runtime.agents["estoque"]._alert_task
    assert worker is not None and not worker.done()
    runtime.stop()
    assert worker.done()
    assert runtime.agents["estoque"].pending == {}


def test_worker_agrupa_repeticoes_pendentes(automatic_ready, runtime, monkeypatch):
    _, product, _ = automatic_ready
    wait_alerts(runtime)
    checked = []
    async def evaluate(product_id, *args, **kwargs):
        checked.append(product_id)
    monkeypatch.setattr(runtime.agents["estoque"], "sincronizar_alerta_preventivo", evaluate)
    async def queue():
        agent = runtime.agents["estoque"]
        for _ in range(10):
            agent.agendar_alertas({product})
        await agent.aguardar_alertas()
    runtime.call(queue)
    assert checked == [product]


def test_aviso_de_publicacao_de_outro_agente_e_rejeitado(automatic_ready, runtime):
    from app.agents.protocol import PREVISAO_PUBLICADA
    with pytest.raises(RemoteFailure) as error:
        AgentGateway(runtime).request("atendimento", "estoque", PREVISAO_PUBLICADA,
            {"execucao_id": "previsao-" + "a" * 32})
    assert error.value.code == "UNSUPPORTED_ONTOLOGY"


def test_reposicao_durante_rpc_e_considerada_na_avaliacao_final(ready, runtime, monkeypatch):
    service, product, _ = ready
    original_day = preventive._hoje
    monkeypatch.setattr(preventive, "_hoje", lambda value: original_day(date(2026, 1, 11) if value is None else value))
    original_query = runtime.agents["previsao"].service.consultar
    started, release = Event(), Event()
    def slow_query(**kwargs):
        result = original_query(**kwargs)
        started.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(runtime.agents["previsao"].service, "consultar", slow_query)
    gateway = AgentGateway(runtime)
    runtime.call(lambda: runtime.agents["previsao"].aguardar_inicializacao())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(gateway.sincronizar_alerta_preventivo, product)
        try:
            assert started.wait(3)
            gateway.executar_estoque("movimentar", tipo_item="produto", item_id=product,
                                    tipo_movimentacao="entrada", quantidade=20, motivo="Reposição fictícia")
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result["disponivel"] == 25 and result["saldo_projetado"] == "15"
    assert result["acao"] == "sem_alerta" and not result["risco"]
    assert alert_rows(service) == []
