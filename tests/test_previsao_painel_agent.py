"""Painel trafega por SPADE e roda em thread, sem agendar recálculos."""

import asyncio
from threading import get_ident

from app.agents.gateway import AgentGateway
from app.agents.protocol import PREVISAO_PAINEL
from app.services.previsao_service import PrevisaoError
from test_previsao_agent import FictitiousService, make_agent, request
from test_previsao_painel_protocol import panel_response


def test_painel_e_executado_fora_do_loop_sem_agendar_job(make_agent):
    async def run():
        calls = []
        class Service(FictitiousService):
            def painel(self, **kwargs):
                calls.append((get_ident(), kwargs))
                return panel_response()
            def recalcular(self, *args, **kwargs):
                raise AssertionError("Consulta nunca recalcula.")
        agent = make_agent(Service())
        await agent.setup()
        payload = {"codigo_produto": None, "granularidade": "diaria", "data_inicio": None, "data_fim": None}
        response = await request(agent, PREVISAO_PAINEL, payload)
        assert response.performative == "inform" and response.payload == panel_response()
        assert calls[0][0] != get_ident() and calls[0][1] == payload
        assert agent._active_job is None and agent._jobs == {}
    asyncio.run(run())


def test_guardas_de_inicializacao_e_resposta_invalida_sao_seguras(make_agent):
    async def run():
        class Service(FictitiousService):
            def painel(self, **kwargs):
                return {"segredo": "não expor"}
        agent = make_agent(Service())
        payload = {"codigo_produto": None, "granularidade": "diaria", "data_inicio": None, "data_fim": None}
        assert (await request(agent, PREVISAO_PAINEL, payload)).payload["code"] == "FORECAST_INITIALIZING"
        await agent.setup()
        response = await request(agent, PREVISAO_PAINEL, payload)
        assert response.payload["code"] == "INTERNAL_ERROR" and "segredo" not in str(response.payload)
        await agent.stop()
        assert (await request(agent, PREVISAO_PAINEL, payload)).payload["code"] == "FORECAST_UNAVAILABLE"
    asyncio.run(run())


def test_erro_de_inicializacao_e_respeitado_pelo_painel(make_agent):
    async def run():
        class Service(FictitiousService):
            def inicializar(self, **kwargs):
                raise PrevisaoError("INVALID_ARTIFACT", "Artefato fictício inválido.")
            def painel(self, **kwargs):
                raise AssertionError("Inicialização falhou.")
        agent = make_agent(Service())
        await agent.setup()
        payload = {"codigo_produto": None, "granularidade": "diaria", "data_inicio": None, "data_fim": None}
        response = await request(agent, PREVISAO_PAINEL, payload)
        assert response.payload["code"] == "INVALID_ARTIFACT"
    asyncio.run(run())


def test_gateway_envia_nova_consulta_para_previsao_pelo_estoque():
    calls = []
    gateway = AgentGateway(None)
    gateway.request = lambda *args: calls.append(args) or "resultado fictício"
    assert gateway.consultar_painel_previsao("00100", "mensal", "2030-01-02", "2030-01-05") == "resultado fictício"
    assert calls == [("estoque", "previsao", PREVISAO_PAINEL, {
        "codigo_produto": "00100", "granularidade": "mensal", "data_inicio": "2030-01-02", "data_fim": "2030-01-05"})]
