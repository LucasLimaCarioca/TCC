"""Ponte síncrona para Flask; as corrotinas executam no loop dedicado do runtime."""

from app.agents.protocol import (BAIXA_VENDA, DISPONIBILIDADE, PING, PREVISAO,
                                 PREVISAO_ATUALIZAR, PREVISAO_STATUS)
from uuid import uuid4


class AgentGateway:
    def __init__(self, runtime):
        self.runtime = runtime

    def responder(self, mensagem, cliente_nome="Cliente Simulado"):
        return self.runtime.call(lambda: self.runtime.agents["atendimento"].responder(
            mensagem, cliente_nome=cliente_nome))

    def request(self, source, target, ontology, payload):
        return self.runtime.call(lambda: self.runtime.request(source, target, ontology, payload))

    def ping(self, source, target):
        return self.request(source, target, PING, {})

    def consultar_disponibilidade(self, items):
        return self.request("atendimento", "estoque", DISPONIBILIDADE, {"items": items})

    def baixar_estoque(self, items, cliente_nome, operacao_id=None):
        return self.request("atendimento", "estoque", BAIXA_VENDA,
                            {"items": items, "cliente_nome": cliente_nome,
                             "operacao_id": operacao_id or str(uuid4())})

    def executar_estoque(self, command, **kwargs):
        return self.runtime.call(lambda: self.runtime.agents["estoque"].executar(command, **kwargs))

    def consultar_risco(self, produto_id, granularidade="diaria", horizonte_dias=None):
        return self.runtime.call(lambda: self.runtime.agents["estoque"].consultar_risco(
            produto_id, granularidade, horizonte_dias, self.runtime.config.request_timeout))

    def sincronizar_alerta_preventivo(self, produto_id, horizonte_dias=None):
        return self.runtime.call(lambda: self.runtime.agents["estoque"].sincronizar_alerta_preventivo(
            produto_id, horizonte_dias, self.runtime.config.request_timeout))

    def consultar_previsao(self, produto_id=None, granularidade="diaria", horizonte_dias=7):
        return self.request("estoque", "previsao", PREVISAO, {
            "produto_id": produto_id, "granularidade": granularidade,
            "horizonte_dias": horizonte_dias,
        })

    def atualizar_previsao(self, data_corte, horizonte_dias=28):
        return self.request("estoque", "previsao", PREVISAO_ATUALIZAR, {
            "data_corte": data_corte, "horizonte_dias": horizonte_dias,
        })

    def status_previsao(self, job_id):
        return self.request("estoque", "previsao", PREVISAO_STATUS, {"job_id": job_id})


def start_runtime(app):
    """Ativar uma vez por app. O factory e o reloader pai não chamam este método."""
    from app.agents.runtime import AgentRuntime

    runtime = app.extensions.get("agent_runtime")
    if runtime is None:
        runtime = AgentRuntime(app)
        app.extensions["agent_runtime"] = runtime
    runtime.start()
    app.extensions["agent_gateway"] = AgentGateway(runtime)
    return runtime
