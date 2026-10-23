"""Notificações e inicialização usam serviços fictícios, sem rede ou dados reais."""

import asyncio
import concurrent.futures
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agents.base_agent import AgentTimeout, AgentUnavailable, BaseAgent, RemoteFailure
from app.agents.config import RuntimeConfig
from app.agents.previsao_agent import PrevisaoAgent
from app.agents.protocol import JIDS, PREVISAO_PUBLICADA, ProtocolError, encode
from app.agents.runtime import AgentRuntime
from app.services.previsao_service import PrevisaoError


RUN_ID = "previsao-0123456789abcdef0123456789abcdef"


class FictitiousService:
    def inicializar(self, *, cancel_event=None):
        pass

    def recalcular(self, data_corte, horizonte_dias, *, cancel_event):
        return {"run_id": RUN_ID, "status": "concluido"}


@pytest.fixture
def make_agent(monkeypatch):
    monkeypatch.setattr(BaseAgent, "__init__", lambda *args, **kwargs: None)

    async def no_transport(self):
        pass

    monkeypatch.setattr(BaseAgent, "setup", no_transport)
    monkeypatch.setattr(BaseAgent, "stop", no_transport)
    return lambda service=None: PrevisaoAgent("senha-ficticia", None, service=service or FictitiousService())


async def start_job(agent):
    await agent.setup()
    await agent.aguardar_inicializacao()
    scheduled = agent._schedule({"data_corte": "2030-01-01", "horizonte_dias": 1})
    await agent._job_task
    return scheduled["job_id"]


def test_notificacao_separada_nao_atrasa_conclusao_e_confirma_modelo_ativo(make_agent):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        agent = make_agent()

        async def request(target, ontology, payload, timeout):
            # O aviso precisa atravessar o contrato real antes de ser enviado.
            encode(JIDS[target], ontology, payload)
            calls.append((target, ontology, payload, timeout))
            assert list(agent._jobs.values())[-1]["status"] == "concluido"
            started.set()
            await release.wait()

        agent.request = request
        # O job encerra mesmo se o RPC de notificação continuar sem resposta.
        job_id = await asyncio.wait_for(start_job(agent), 1)
        await asyncio.wait_for(started.wait(), 1)
        assert agent._status(job_id) == {"job_id": job_id, "status": "concluido",
                                       "execucao_id": RUN_ID, "erro": None}
        assert agent._active_job is None
        assert calls == [("estoque", PREVISAO_PUBLICADA, {"execucao_id": RUN_ID}, 5)]
        assert len(agent._notification_tasks) == 1
        release.set()
        await asyncio.gather(*tuple(agent._notification_tasks))
        await asyncio.sleep(0)
        assert not agent._notification_tasks
        await agent.stop()

    asyncio.run(run())


@pytest.mark.parametrize("error", [
    AgentTimeout("Timeout fictício."), AgentUnavailable("Desconectado fictício."),
    RemoteFailure({"code": "INTERNAL_ERROR", "message": "Falha fictícia."}),
    ProtocolError("Contrato fictício."), RuntimeError("SEGREDO_FICTICIO_NAO_REGISTRAR"),
])
def test_falha_do_aviso_nao_reverte_job_concluido_e_nao_expoe_detalhes(make_agent, caplog, error):
    async def run():
        agent = make_agent()
        calls = []

        async def request(*args, **kwargs):
            calls.append(True)
            raise error

        agent.request = request
        job_id = await start_job(agent)
        await asyncio.gather(*tuple(agent._notification_tasks))
        assert agent._status(job_id)["status"] == "concluido"
        assert agent._status(job_id)["execucao_id"] == RUN_ID
        assert agent._status(job_id)["erro"] is None and agent._active_job is None
        assert calls == [True]
        assert "SEGREDO_FICTICIO" not in caplog.text
        await agent.stop()

    asyncio.run(run())


def test_stop_cancela_aviso_pendente_antes_de_desconectar(make_agent, monkeypatch):
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        agent = make_agent()

        async def request(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def stop_transport(self):
            assert cancelled.is_set()

        monkeypatch.setattr(BaseAgent, "stop", stop_transport)
        agent.request = request
        job_id = await start_job(agent)
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(agent.stop(), 1)
        assert cancelled.is_set() and not agent._notification_tasks
        assert agent._status(job_id)["status"] == "concluido"

    asyncio.run(run())


def test_job_falho_nao_notifica_publicacao(make_agent):
    async def run():
        class Service(FictitiousService):
            def recalcular(self, *args, **kwargs):
                raise PrevisaoError("FORECAST_CANCELLED", "Cancelamento fictício.")

        agent = make_agent(Service())

        async def unexpected_request(*args, **kwargs):
            pytest.fail("Job falho não pode anunciar publicação.")

        agent.request = unexpected_request
        job_id = await start_job(agent)
        assert agent._status(job_id)["status"] == "falhou"
        assert not agent._notification_tasks
        await agent.stop()

    asyncio.run(run())


def test_identificador_ficticio_legado_invalido_no_aviso_nao_muda_job(make_agent):
    async def run():
        class Service(FictitiousService):
            def recalcular(self, *args, **kwargs):
                return {"run_id": "previsao-ficticia-legada", "status": "concluido"}

        agent = make_agent(Service())

        async def validate_request(target, ontology, payload, timeout):
            encode(JIDS[target], ontology, payload)

        agent.request = validate_request
        job_id = await start_job(agent)
        await asyncio.gather(*tuple(agent._notification_tasks))
        assert agent._status(job_id)["status"] == "concluido"
        await agent.stop()

    asyncio.run(run())


def fictitious_runtime(monkeypatch, events, *, fail_initialization=False):
    """Executa o lifecycle real com agentes e Container inteiramente falsos."""
    release = asyncio.Event()
    started = asyncio.Event()

    class Agent:
        def __init__(self, role):
            self.role = role
            self.jid = JIDS[role]

        async def start(self):
            events.append("online:" + self.role)

        async def stop(self):
            events.append("stop:" + self.role)

        async def aguardar_inicializacao(self):
            started.set()
            try:
                await release.wait()
                if fail_initialization:
                    raise RuntimeError("SEGREDO_FICTICIO_NAO_REGISTRAR")
                events.append("initialized")
            except asyncio.CancelledError:
                events.append("initialization_wait_cancelled")
                raise

        def agendar_alertas(self):
            events.append("scheduled")

    monkeypatch.setattr("spade.container.Container", lambda: SimpleNamespace(unregister=lambda jid: None))
    monkeypatch.setattr("app.agents.atendimento_agent.AtendimentoSPADEAgent",
                        lambda *args, **kwargs: Agent("atendimento"))
    monkeypatch.setattr("app.agents.estoque_agent.EstoqueAgent",
                        lambda *args, **kwargs: Agent("estoque"))
    monkeypatch.setattr("app.agents.previsao_agent.PrevisaoAgent",
                        lambda *args, **kwargs: Agent("previsao"))
    runtime = AgentRuntime(SimpleNamespace(logger=Mock()), RuntimeConfig({name: "senha-ficticia" for name in JIDS}))

    async def no_server():
        pass

    runtime._start_server = no_server
    runtime._ready = concurrent.futures.Future()
    runtime._stopping = asyncio.Event()
    return runtime, started, release


def test_runtime_fica_online_sem_esperar_modelo_e_agenda_apos_inicializacao(monkeypatch):
    async def run():
        events = []
        runtime, started, release = fictitious_runtime(monkeypatch, events)
        lifecycle = asyncio.create_task(runtime._lifecycle())
        await asyncio.wait_for(started.wait(), 1)
        assert runtime.running and runtime._ready.done() and runtime._ready.exception() is None
        assert events == ["online:atendimento", "online:estoque", "online:previsao"]
        assert not runtime._predictive_startup_task.done()
        release.set()
        await asyncio.wait_for(runtime._predictive_startup_task, 1)
        assert events[-2:] == ["initialized", "scheduled"]
        runtime._stopping.set()
        await asyncio.wait_for(lifecycle, 1)
        assert not runtime.running and runtime._predictive_startup_task is None

    asyncio.run(run())


def test_shutdown_cancela_espera_inicial_antes_de_parar_agentes(monkeypatch):
    async def run():
        events = []
        runtime, started, _ = fictitious_runtime(monkeypatch, events)
        lifecycle = asyncio.create_task(runtime._lifecycle())
        await asyncio.wait_for(started.wait(), 1)
        runtime._stopping.set()
        await asyncio.wait_for(lifecycle, 1)
        assert "scheduled" not in events
        assert events.index("initialization_wait_cancelled") < events.index("stop:atendimento")
        assert runtime._predictive_startup_task is None

    asyncio.run(run())


def test_falha_de_carga_nao_desliga_runtime_nem_expoe_conteudo(monkeypatch):
    async def run():
        events = []
        runtime, started, release = fictitious_runtime(monkeypatch, events, fail_initialization=True)
        lifecycle = asyncio.create_task(runtime._lifecycle())
        await asyncio.wait_for(started.wait(), 1)
        release.set()
        await asyncio.wait_for(runtime._predictive_startup_task, 1)
        assert runtime.running and "scheduled" not in events
        runtime.app.logger.warning.assert_called_once_with(
            "Não foi possível agendar a avaliação inicial de alertas preventivos."
        )
        runtime._stopping.set()
        await asyncio.wait_for(lifecycle, 1)

    asyncio.run(run())


def test_runtime_toleras_stubs_sem_api_de_alertas(monkeypatch):
    async def run():
        runtime = AgentRuntime(SimpleNamespace(logger=Mock()), RuntimeConfig({name: "senha-ficticia" for name in JIDS}))
        runtime.agents = {"estoque": object(), "previsao": object()}
        await runtime._iniciar_alertas_preventivos()
        runtime.app.logger.warning.assert_not_called()

    asyncio.run(run())
