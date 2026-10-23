"""Ciclo de jobs do agente; serviços e registros inteiramente fictícios."""

import asyncio
from threading import Event, get_ident
from uuid import uuid4

import pytest

from app.agents.base_agent import BaseAgent
from app.agents.previsao_agent import PrevisaoAgent
from app.agents.protocol import (PREVISAO, PREVISAO_ATUALIZAR, PREVISAO_STATUS,
                                 decode, encode)
from app.services.previsao_service import PrevisaoError


def fictitious_result(product=1):
    return {"produto_id": product, "codigo_produto": "FICTICIO-001" if product else None,
        "granularidade": "diaria", "horizonte_dias": 1,
        "data_corte": "2030-01-01", "cobertura_inicio": "2030-01-02", "cobertura_fim": "2030-01-02",
        "total_previsto": "0.1", "periodos": [{"periodo_inicio": "2030-01-02", "periodo_fim": "2030-01-02",
            "cobertura_inicio": "2030-01-02", "cobertura_fim": "2030-01-02",
            "parcial": False, "quantidade_prevista": "0.1"}],
        "modelo": "naive" if product else {"FICTICIO-001": "naive"},
        "gerada_em": "2030-01-01T00:00:00-04:00", "versao": "ficticia-v1", "execucao_id": "previsao-ficticia"}


class FictitiousService:
    def inicializar(self, *, cancel_event=None):
        return None

    def consultar(self, produto_id=None, granularidade="diaria", horizonte_dias=1):
        return fictitious_result(produto_id)

    def recalcular(self, data_corte, horizonte_dias, *, cancel_event):
        return {"run_id": "previsao-ficticia-nova", "status": "concluido"}


@pytest.fixture
def make_agent(monkeypatch):
    # A integração com SPADE real está coberta em test_agent_runtime.py.
    monkeypatch.setattr(BaseAgent, "__init__", lambda *args, **kwargs: None)

    async def no_transport(self):
        pass

    monkeypatch.setattr(BaseAgent, "setup", no_transport)
    monkeypatch.setattr(BaseAgent, "stop", no_transport)

    def construct(service=None):
        agent = PrevisaoAgent("senha-ficticia", None, service=service or FictitiousService())
        agent.INITIALIZATION_WAIT = 0.01
        return agent

    return construct


async def request(agent, ontology, payload):
    message = encode("previsao@localhost", ontology, payload)
    response = decode(await agent.handle_request("estoque@localhost", decode(message)))
    assert response.thread == message.thread and response.ontology == ontology
    return response


async def query(agent, product=1):
    return await request(agent, PREVISAO, {"produto_id": product, "granularidade": "diaria", "horizonte_dias": 1})


async def schedule(agent):
    return await request(agent, PREVISAO_ATUALIZAR, {"data_corte": "2030-01-01", "horizonte_dias": 1})


def test_consulta_geral_e_produto_sao_executadas_fora_do_loop(make_agent):
    async def run():
        threads = []

        class Service(FictitiousService):
            def inicializar(self, *, cancel_event=None):
                threads.append(get_ident())

            def consultar(self, **kwargs):
                threads.append(get_ident())
                return super().consultar(**kwargs)

        agent = make_agent(Service())
        await agent.setup()
        await agent.aguardar_inicializacao()
        assert (await query(agent)).payload == fictitious_result(1)
        assert (await query(agent, None)).payload == fictitious_result(None)
        assert len(threads) == 3 and all(thread != get_ident() for thread in threads)

    asyncio.run(run())


def test_inicializacao_lenta_nao_bloqueia_inbox(make_agent):
    async def run():
        release = Event()

        class Service(FictitiousService):
            def inicializar(self, *, cancel_event=None):
                assert release.wait(3)

        agent = make_agent(Service())
        await agent.setup()
        try:
            response = await asyncio.wait_for(query(agent), timeout=0.5)
            assert response.performative == "failure" and response.payload["code"] == "FORECAST_INITIALIZING"
            response = await schedule(agent)
            assert response.payload["code"] == "FORECAST_INITIALIZING"
            assert agent._active_job is None
        finally:
            release.set()
            await agent.aguardar_inicializacao()
        assert (await query(agent)).performative == "inform"

    asyncio.run(run())


def test_erro_inicializacao_e_seguro_e_recalculo_recupera(make_agent):
    async def run():
        class Service(FictitiousService):
            def inicializar(self, *, cancel_event=None):
                raise PrevisaoError("INVALID_ARTIFACT", "Artefato fictício inválido.")

        agent = make_agent(Service())
        await agent.setup()
        await agent.aguardar_inicializacao()
        assert (await query(agent)).payload["code"] == "INVALID_ARTIFACT"
        scheduled = await schedule(agent)
        assert scheduled.performative == "inform"
        await agent._job_task
        assert (await query(agent)).performative == "inform"
        status = await request(agent, PREVISAO_STATUS, {"job_id": scheduled.payload["job_id"]})
        assert status.payload["status"] == "concluido"

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["inicializar", "consultar", "recalcular"])
def test_excecao_inesperada_nao_expoe_detalhes(make_agent, operation):
    async def run():
        service = FictitiousService()

        def fail(*args, **kwargs):
            raise RuntimeError("segredo-ficticio-nao-deve-sair")

        setattr(service, operation, fail)
        agent = make_agent(service)
        await agent.setup()
        await agent.aguardar_inicializacao()
        if operation == "recalcular":
            scheduled = await schedule(agent)
            await agent._job_task
            response = await request(agent, PREVISAO_STATUS, {"job_id": scheduled.payload["job_id"]})
            assert response.payload["status"] == "falhou"
            assert response.payload["erro"]["code"] == "INTERNAL_ERROR"
        else:
            response = await query(agent)
            assert response.payload["code"] == "INTERNAL_ERROR"
        assert "segredo-ficticio" not in str(response.payload)

    asyncio.run(run())


def test_apenas_um_job_por_vez_sem_bloquear_consulta(make_agent):
    async def run():
        started, release = Event(), Event()

        class Service(FictitiousService):
            def recalcular(self, *args, **kwargs):
                started.set()
                assert release.wait(3)
                return super().recalcular(*args, **kwargs)

        agent = make_agent(Service())
        await agent.setup()
        await agent.aguardar_inicializacao()
        try:
            first, second = await asyncio.gather(schedule(agent), schedule(agent))
            assert {first.performative, second.performative} == {"inform", "failure"}
            failure = first if first.performative == "failure" else second
            scheduled = second if first.performative == "failure" else first
            assert failure.payload["code"] == "FORECAST_BUSY"
            assert await asyncio.to_thread(started.wait, 1)
            assert (await query(agent)).performative == "inform"
            status = await request(agent, PREVISAO_STATUS, {"job_id": scheduled.payload["job_id"]})
            assert status.payload["status"] == "em_execucao" and status.payload["erro"] is None
        finally:
            release.set()
            await agent._job_task
        status = await request(agent, PREVISAO_STATUS, {"job_id": scheduled.payload["job_id"]})
        assert status.payload["status"] == "concluido" and agent._active_job is None

    asyncio.run(run())


def test_falha_tipificada_libera_novo_job(make_agent):
    async def run():
        class Service(FictitiousService):
            def recalcular(self, *args, **kwargs):
                raise PrevisaoError("REVIEW_UNAVAILABLE", "Revisão fictícia ausente.")

        agent = make_agent(Service())
        await agent.setup()
        scheduled = await schedule(agent)
        await agent._job_task
        status = await request(agent, PREVISAO_STATUS, {"job_id": scheduled.payload["job_id"]})
        assert status.payload == {"job_id": scheduled.payload["job_id"], "status": "falhou",
            "execucao_id": None, "erro": {"code": "REVIEW_UNAVAILABLE", "message": "Revisão fictícia ausente."}}
        agent.service = FictitiousService()
        assert (await schedule(agent)).performative == "inform"
        await agent._job_task
        assert agent._active_job is None

    asyncio.run(run())


def test_encerramento_sinaliza_cancelamento_e_recusa_novos_jobs(make_agent):
    async def run():
        started = Event()

        class Service(FictitiousService):
            def recalcular(self, data_corte, horizonte_dias, *, cancel_event):
                started.set()
                assert cancel_event.wait(3)
                return {"run_id": "previsao-nao-ativada", "status": "concluido"}

        agent = make_agent(Service())
        await agent.setup()
        scheduled = await schedule(agent)
        assert await asyncio.to_thread(started.wait, 1)
        await agent.stop()
        await agent._job_task
        job = agent._jobs[scheduled.payload["job_id"]]
        assert job["status"] == "falhou" and job["execucao_id"] is None
        assert job["erro"]["code"] == "FORECAST_CANCELLED"
        assert (await schedule(agent)).payload["code"] == "FORECAST_UNAVAILABLE"

    asyncio.run(run())


def test_stop_durante_espera_inicializacao_nao_agenda_job_tardio(make_agent):
    async def run():
        release = Event()
        initialization_cancel = []
        recalculations = []

        class Service(FictitiousService):
            def inicializar(self, *, cancel_event=None):
                initialization_cancel.append(cancel_event)
                assert release.wait(3)

            def recalcular(self, *args, **kwargs):
                recalculations.append(True)
                return super().recalcular(*args, **kwargs)

        agent = make_agent(Service())
        agent.INITIALIZATION_WAIT = 1
        await agent.setup()
        pending = asyncio.create_task(schedule(agent))
        await asyncio.sleep(0)
        try:
            await agent.stop()
            assert agent._lifecycle_cancel.is_set()
        finally:
            release.set()
        response = await pending
        assert response.performative == "failure" and response.payload["code"] == "FORECAST_UNAVAILABLE"
        assert initialization_cancel == [agent._lifecycle_cancel]
        assert agent._jobs == {} and recalculations == []

    asyncio.run(run())


def test_stop_antes_da_thread_nao_inicia_recalculo(make_agent):
    async def run():
        recalculations = []

        class Service(FictitiousService):
            def recalcular(self, *args, **kwargs):
                recalculations.append(True)
                return super().recalcular(*args, **kwargs)

        agent = make_agent(Service())
        await agent.setup()
        await agent.aguardar_inicializacao()
        scheduled = await schedule(agent)
        await agent.stop()
        await agent._job_task
        assert recalculations == []
        job = agent._jobs[scheduled.payload["job_id"]]
        assert job["status"] == "falhou" and job["erro"]["code"] == "FORECAST_CANCELLED"

    asyncio.run(run())


def test_jobs_expiram_e_nao_sao_retomados_ao_reiniciar(make_agent):
    async def run():
        agent = make_agent()
        agent.MAX_JOBS = 2
        await agent.setup()
        jobs = []
        for _ in range(3):
            jobs.append((await schedule(agent)).payload["job_id"])
            await agent._job_task
        assert len(agent._jobs) == 2
        assert (await request(agent, PREVISAO_STATUS, {"job_id": jobs[0]})).payload["code"] == "JOB_NOT_FOUND"
        assert (await request(agent, PREVISAO_STATUS, {"job_id": str(uuid4())})).payload["code"] == "JOB_NOT_FOUND"
        restarted = make_agent()
        await restarted.setup()
        await restarted.aguardar_inicializacao()
        assert (await request(restarted, PREVISAO_STATUS, {"job_id": jobs[-1]})).payload["code"] == "JOB_NOT_FOUND"

    asyncio.run(run())


def test_resposta_invalida_do_servico_vira_falha_segura(make_agent):
    async def run():
        class Service(FictitiousService):
            def consultar(self, **kwargs):
                return {"registro_real": "conteudo-ficticio-restrito"}

        agent = make_agent(Service())
        await agent.setup()
        response = await query(agent)
        assert response.payload["code"] == "INTERNAL_ERROR"
        assert "conteudo-ficticio-restrito" not in str(response.payload)

    asyncio.run(run())
