"""Agente de controle de estoque com operações reais e consulta ao Previsão."""

import asyncio

from app.agents.base_agent import AgentUnavailable, BaseAgent, RemoteFailure
from app.agents.protocol import BAIXA_VENDA, DISPONIBILIDADE, PREVISAO, encode, failure
from app.services import estoque_service
from app.services.estoque_service import EstoqueError


class EstoqueAgent(BaseAgent):
    def __init__(self, password, app, port=5222, audit=None):
        super().__init__("estoque", password, port, audit)
        self.app = app

    async def executar(self, command, **kwargs):
        if not self.is_alive() or not self.client.is_connected():
            raise AgentUnavailable("Agente de estoque desconectado.")
        if command not in {"visao_estoque", "movimentar", "configurar_minimo",
                            "cadastrar_materia_prima", "consultar_disponibilidade", "consultar_produto", "registrar_venda"}:
            raise EstoqueError("INVALID_INPUT", "Operação de estoque inválida.")

        def execute():
            with self.app.app_context():
                return getattr(estoque_service, command)(**kwargs)

        return await asyncio.to_thread(execute)

    async def handle_request(self, sender, envelope):
        try:
            if envelope.ontology == DISPONIBILIDADE:
                result = await self.executar("consultar_disponibilidade", itens=envelope.payload["items"])
            elif envelope.ontology == BAIXA_VENDA:
                result = await self.executar("registrar_venda", itens=envelope.payload["items"],
                    cliente_nome=envelope.payload["cliente_nome"], operacao_id=envelope.payload["operacao_id"])
            else:
                return await super().handle_request(sender, envelope)
            return encode(sender, envelope.ontology, result, "inform", envelope.thread)
        except EstoqueError as error:
            return failure(sender, envelope.ontology, envelope.thread, error.code, str(error))
        except Exception:
            return failure(sender, envelope.ontology, envelope.thread, "INTERNAL_ERROR",
                           "Não foi possível concluir a operação de estoque.")

    async def consultar_risco(self, produto_id, granularidade="diaria", horizonte_dias=7, timeout=5):
        product = await self.executar("consultar_produto", produto_id=produto_id)
        stock = product["disponivel"]
        try:
            forecast = await self.request("previsao", PREVISAO, {"produto_id": produto_id,
                "granularidade": granularidade, "horizonte_dias": horizonte_dias}, timeout)
        except RemoteFailure as error:
            if error.code != "NOT_IMPLEMENTED":
                raise
            return {"produto_id": produto_id, "disponivel": stock, "status": "previsao_indisponivel",
                    "risco": None, "mensagem": "A previsão de demanda será implementada nas fases de previsão."}
        if forecast.payload["produto_id"] != produto_id or forecast.payload["granularidade"] != granularidade:
            raise RemoteFailure({"code": "INVALID_RESPONSE", "message": "Previsão incompatível com a consulta."})
        projected = stock - forecast.payload["total_previsto"]
        return {**product, "saldo_projetado": projected, "status": "ok",
                "risco": projected < product["estoque_minimo"], "previsao": forecast.payload}
