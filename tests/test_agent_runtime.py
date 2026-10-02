"""Integração SPADE/XMPP real: servidor próprio, loopback e banco fictício."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import socket
from uuid import uuid4

import pytest

from app.agents.base_agent import AgentTimeout, AgentUnavailable, RemoteFailure
from app.agents.config import RuntimeConfig
from app.agents.gateway import AgentGateway
from app.agents.protocol import BAIXA_VENDA, DISPONIBILIDADE, JIDS, PING, PREVISAO, ProtocolError, encode
from app.agents.runtime import AgentRuntime
from app.app import create_app
from app.models.log_mensagem_agente import LogMensagemAgente
from app.database import db
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.services import estoque_service


def free_ports():
    # As duas portas são reservadas juntas para não selecionar a mesma porta.
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


@pytest.fixture
def runtime(app):
    existing = app.extensions.get("agent_runtime")
    if existing is not None:
        yield existing
        return
    ports = free_ports()
    config = RuntimeConfig({name: "senha-ficticia" for name in JIDS}, *ports)
    instance = AgentRuntime(app, config)
    instance.start()
    app.extensions["agent_runtime"] = instance
    app.extensions["agent_gateway"] = AgentGateway(instance)
    yield instance
    instance.stop()
    assert not instance.thread.is_alive()
    assert not instance.server.is_alive()
    assert not any(agent.is_alive() for agent in instance.agents.values())


@pytest.mark.parametrize(("source", "target"), [
    ("atendimento", "estoque"), ("estoque", "atendimento"),
    ("estoque", "previsao"), ("previsao", "estoque"),
])
def test_comunicacao_real_bidirecional(runtime, source, target, app):
    response = AgentGateway(runtime).ping(source, target)
    assert response.payload == {"agent": target, "status": "ok"}
    assert response.performative == "inform"
    assert runtime.agents[source].received >= 1
    assert runtime.agents[target].received >= 1
    assert all(runtime.health()["agents"].values())
    with app.app_context():
        logs = LogMensagemAgente.query.filter_by(thread=response.thread).all()
        assert {(log.performative, log.status) for log in logs} == {
            ("request", "sent"), ("request", "received"),
            ("inform", "sent"), ("inform", "received"),
        }
        assert all(log.payload == {"redacted": True} for log in logs)


def test_correlacao_sob_concorrencia(runtime):
    gateway = AgentGateway(runtime)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: gateway.ping("atendimento", "estoque"), range(8)))
    assert len({reply.thread for reply in replies}) == 8
    assert all(reply.payload["agent"] == "estoque" for reply in replies)
    assert runtime.agents["atendimento"].pending == {}


def test_topologia_e_operacoes_futuras(runtime, catalogo):
    gateway = AgentGateway(runtime)
    with pytest.raises(ProtocolError):
        gateway.ping("atendimento", "previsao")
    assert gateway.consultar_disponibilidade([{"produto_id": 1, "quantidade": 2}]).payload["available"] is True
    with pytest.raises(RemoteFailure) as error:
        gateway.consultar_previsao(1)
    assert error.value.code == "NOT_IMPLEMENTED"


def test_estoque_baixa_real_idempotente_e_rejeicao_sem_mutacao(runtime, app, catalogo):
    gateway = AgentGateway(runtime)
    product = catalogo["caixa de 10L - chocolate"]
    items = [{"produto_id": product, "quantidade": 2}, {"produto_id": product, "quantidade": 3}]
    key = str(uuid4())
    first = gateway.baixar_estoque(items, "Cliente Fictício", key)
    repeat = gateway.baixar_estoque(items, "Cliente Fictício", key)
    assert first.payload == repeat.payload
    assert first.thread != repeat.thread
    available = gateway.consultar_disponibilidade([{ "produto_id": product, "quantidade": 16}])
    assert available.payload["available"] is False
    with pytest.raises(RemoteFailure) as error:
        gateway.baixar_estoque([{ "produto_id": product, "quantidade": 16}], "Cliente Fictício")
    assert error.value.code == "INSUFFICIENT_STOCK"
    with app.app_context():
        assert db.session.get(Produto, product).quantidade_disponivel == 15
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 1
        assert MovimentacaoEstoque.query.one().venda_id == Venda.query.one().id
        assert all(log.payload == {"redacted": True} for log in LogMensagemAgente.query.all())


def test_resposta_xmpp_perdida_repeticao_recupera_venda(runtime, app, catalogo, monkeypatch):
    product = catalogo["caixa de 10L - chocolate"]
    payload = {"items": [{"produto_id": product, "quantidade": 2}],
               "cliente_nome": "Cliente Fictício", "operacao_id": str(uuid4())}
    original = runtime.agents["estoque"].send_message
    dropped = False

    async def lose_first(message):
        nonlocal dropped
        if not dropped and message.get_metadata("ontology") == BAIXA_VENDA and message.get_metadata("performative") == "inform":
            dropped = True
            return
        await original(message)

    monkeypatch.setattr(runtime.agents["estoque"], "send_message", lose_first)
    with pytest.raises(AgentTimeout):
        runtime.call(lambda: runtime.agents["atendimento"].request("estoque", BAIXA_VENDA, payload, timeout=0.2))
    assert dropped
    retry = AgentGateway(runtime).baixar_estoque(payload["items"], payload["cliente_nome"], payload["operacao_id"])
    assert len(retry.payload["vendas"]) == 1
    assert runtime.agents["atendimento"].pending == {}
    with app.app_context():
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 1
        assert db.session.get(Produto, product).quantidade_disponivel == 18


def test_atendimento_consulta_e_confirma_via_estoque(runtime, app, conversar):
    assert "20 unidades" in conversar("tem 10l chocolate?")
    conversar("quero 2 caixas de 10L chocolate")
    assert "Venda registrada!" in conversar("sim")
    with app.app_context():
        assert LogMensagemAgente.query.filter_by(ontology=DISPONIBILIDADE, sender=JIDS["atendimento"], status="sent").count() == 2
        assert LogMensagemAgente.query.filter_by(ontology=BAIXA_VENDA, sender=JIDS["atendimento"], status="sent").count() == 1
        assert MovimentacaoEstoque.query.one().quantidade == 2


def test_estoque_consulta_previsao_real_sem_inventar_resultado(runtime, app, catalogo):
    result = AgentGateway(runtime).consultar_risco(catalogo["caixa de 10L - chocolate"])
    assert result["status"] == "previsao_indisponivel" and result["risco"] is None
    with app.app_context():
        assert LogMensagemAgente.query.filter_by(ontology=PREVISAO, sender=JIDS["estoque"], performative="request", status="sent").count() == 1
        assert LogMensagemAgente.query.filter_by(ontology=PREVISAO, sender=JIDS["previsao"], performative="failure", status="received").count() == 1


@pytest.mark.parametrize(("total", "risco"), [(5, False), (15, True)])
def test_estoque_consume_previsao_ficticia_sem_implementar_modelo(runtime, app, catalogo, monkeypatch, total, risco):
    product = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        estoque_service.configurar_minimo("produto", product, 10)

    async def fictitious_forecast(sender, envelope):
        payload = {"produto_id": product, "granularidade": "diaria", "total_previsto": total,
                   "periodos": [{"data": "2030-01-01", "quantidade": total}], "modelo": "ficticio",
                   "gerada_em": "2030-01-01T00:00:00"}
        return encode(sender, PREVISAO, payload, "inform", envelope.thread)

    monkeypatch.setattr(runtime.agents["previsao"], "handle_request", fictitious_forecast)
    result = AgentGateway(runtime).consultar_risco(product)
    assert result["status"] == "ok" and result["risco"] is risco
    assert result["saldo_projetado"] == 20 - total


def test_timeout_nao_deixa_pedido_pendente(runtime):
    async def request():
        # Simula agente conectado que deixou de consumir mensagens.
        runtime.agents["estoque"].inbox.kill()
        await runtime.agents["estoque"].inbox.join(timeout=1)
        return await runtime.agents["atendimento"].request("estoque", PING, {}, timeout=0.1)
    with pytest.raises(AgentTimeout):
        runtime.call(request)
    assert runtime.agents["atendimento"].pending == {}


def test_agente_offline_e_http_503(runtime, client):
    runtime.call(runtime.agents["atendimento"].stop)
    response = client.post("/api/atendimento", json={"mensagem": "oi"})
    assert response.status_code == 503
    assert "erro" in response.get_json()
    with pytest.raises(AgentUnavailable):
        AgentGateway(runtime).ping("estoque", "atendimento")


def test_pedido_e_confirmacao_via_gateway(runtime, conversar):
    assert "Total: R$ 204.00" in conversar("quero 2 caixas de 10L chocolate")
    assert "Total do pedido: R$ 204.00" in conversar("sim")


def test_mensagem_invalida_retorna_failure_correlacionada(runtime):
    async def send_invalid():
        agent = runtime.agents["atendimento"]
        message = encode(JIDS["estoque"], PING, {})
        message.body = "{"
        future = asyncio.get_running_loop().create_future()
        agent.pending[message.thread] = (JIDS["estoque"], PING, future)
        try:
            await agent.send_message(message)
            response = await asyncio.wait_for(future, 3)
            assert response.thread == message.thread
            return response
        finally:
            agent.pending.pop(message.thread, None)
    response = runtime.call(send_invalid)
    assert response.performative == "failure"
    assert response.payload["code"] == "INVALID_MESSAGE"


def test_start_stop_idempotentes_e_reinicio(runtime):
    thread = runtime.thread
    server = runtime.server
    assert runtime.start() is runtime
    assert runtime.thread is thread and runtime.server is server
    runtime.stop()
    runtime.stop()
    assert runtime.health()["running"] is False
    with pytest.raises(AgentUnavailable):
        AgentGateway(runtime).ping("atendimento", "estoque")
    runtime.start()
    assert AgentGateway(runtime).ping("estoque", "previsao").payload["status"] == "ok"


def test_factory_nao_inicia_runtime_e_exclusividade(runtime):
    second_app = create_app({"TESTING": True})
    assert "agent_runtime" not in second_app.extensions
    other = AgentRuntime(second_app, runtime.config)
    with pytest.raises(AgentUnavailable):
        other.start()


def test_ontology_de_outro_agente_nao_e_consumida(runtime):
    with pytest.raises(RemoteFailure) as error:
        AgentGateway(runtime).request("estoque", "atendimento", PREVISAO, {
            "produto_id": 1, "granularidade": "diaria", "horizonte_dias": 7,
        })
    assert error.value.code == "UNSUPPORTED_ONTOLOGY"


def test_servidor_offline_e_status_http(runtime, client):
    assert all(client.get("/api/agentes/status").get_json()["agents"].values())
    runtime.server.kill()  # Falha abrupta, distinta do encerramento normal.
    runtime.server.join(timeout=2)
    assert not client.get("/api/agentes/status").get_json()["running"]
    assert client.post("/api/atendimento", json={"mensagem": "oi"}).status_code == 503


def test_porta_ocupada_falha_sem_deixar_runtime(app):
    existing = app.extensions.get("agent_runtime")
    if existing:
        existing.stop()
    ports = free_ports()
    config = RuntimeConfig({name: "senha-ficticia" for name in JIDS}, *ports)
    instance = AgentRuntime(app, config)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", ports[0]))
        occupied.listen()
        with pytest.raises(AgentUnavailable):
            instance.start()
        assert not instance.thread.is_alive()
        assert not instance.server.is_alive()
    try:
        instance.start()
        assert AgentGateway(instance).ping("atendimento", "estoque").payload["status"] == "ok"
    finally:
        instance.stop()


def test_resposta_so_resolve_thread_sender_e_ontology_corretos(runtime):
    async def verify():
        agent = runtime.agents["estoque"]
        future = asyncio.get_running_loop().create_future()
        expected = encode(JIDS["estoque"], PING, {"agent": "previsao", "status": "ok"}, "inform")
        agent.pending[expected.thread] = (JIDS["previsao"], PING, future)
        try:
            expected.sender = JIDS["atendimento"]
            await agent.consume(expected)
            assert not future.done()
            wrong = encode(JIDS["estoque"], PING, {"agent": "previsao", "status": "ok"}, "inform")
            wrong.sender = JIDS["previsao"]
            await agent.consume(wrong)
            assert not future.done()
            from app.agents.protocol import failure
            wrong = failure(JIDS["estoque"], PREVISAO, expected.thread, "EXAMPLE", "Erro fictício.")
            wrong.sender = JIDS["previsao"]
            await agent.consume(wrong)
            assert not future.done()
            expected.sender = JIDS["previsao"]
            await agent.consume(expected)
            assert future.result().thread == expected.thread
        finally:
            agent.pending.pop(expected.thread, None)
    runtime.call(verify)
