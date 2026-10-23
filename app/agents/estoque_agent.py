"""Agente de controle de estoque com operações reais e consulta ao Previsão."""

import asyncio
from datetime import datetime
from decimal import Decimal, localcontext
from threading import Event
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base_agent import AgentUnavailable, BaseAgent, RemoteFailure
from app.agents.protocol import BAIXA_VENDA, DISPONIBILIDADE, PREVISAO, encode, failure
from app.services import estoque_service
from app.services.estoque_service import EstoqueError
from app.services.alerta_preventivo_config import automatico_ativo, horizonte_persistente, horizonte_preventivo


class EstoqueAgent(BaseAgent):
    def __init__(self, password, app, port=5222, audit=None):
        super().__init__("estoque", password, port, audit)
        self.app = app
        self._alert_task = None
        self._pending_alerts = set()
        self._all_alerts = False
        self._alerts_stopped = False
        self._alerts_cancel = Event()

    async def setup(self):
        await super().setup()
        self._alert_wake = asyncio.Event()
        self._alert_idle = asyncio.Event()
        self._alert_idle.set()
        self._alerts_automatic = automatico_ativo(self.app)
        horizonte_preventivo(self.app)
        if self._alerts_automatic:
            self._alert_task = asyncio.create_task(self._reconciliar_alertas())

    def agendar_alertas(self, produto_ids=None):
        """Agrupa solicitações sem aguardar outra mensagem dentro da Inbox."""
        if self._alerts_stopped or not getattr(self, "_alerts_automatic", False):
            return
        if produto_ids is None:
            self._all_alerts = True
        else:
            self._pending_alerts.update(produto_ids)
        self._alert_idle.clear()
        self._alert_wake.set()

    def _produtos_associados(self):
        from app.models.produto import Produto
        from app.database import db
        with self.app.app_context(), Session(db.engine) as session:
            return set(session.scalars(select(Produto.id).where(
                Produto.ativo.is_(True), Produto.codigo_externo.is_not(None))).all())

    async def _reconciliar_alertas(self):
        try:
            while True:
                await self._alert_wake.wait()
                self._alert_wake.clear()
                products, self._pending_alerts = self._pending_alerts, set()
                all_products, self._all_alerts = self._all_alerts, False
                if all_products:
                    try:
                        products.update(await asyncio.to_thread(self._produtos_associados))
                    except Exception:
                        self.app.logger.warning("Não foi possível iniciar a revisão de alertas preventivos.")
                for product_id in sorted(products):
                    if self._alerts_stopped:
                        return
                    try:
                        await self.sincronizar_alerta_preventivo(product_id)
                    except (EstoqueError, AgentUnavailable, RemoteFailure):
                        # Risco desconhecido preserva o alerta anterior; não é demanda zero.
                        pass
                    except Exception:
                        self.app.logger.warning("Não foi possível atualizar um alerta preventivo.")
                if not self._pending_alerts and not self._all_alerts:
                    self._alert_idle.set()
        finally:
            self._alert_idle.set()

    async def aguardar_alertas(self):
        """Barreira usada na validação; não bloqueia a Inbox nem uma venda."""
        await self._alert_idle.wait()

    async def executar(self, command, **kwargs):
        if not self.is_alive() or not self.client.is_connected():
            raise AgentUnavailable("Agente de estoque desconectado.")
        if command not in {"visao_estoque", "sincronizar_alertas", "movimentar", "configurar_minimo",
                            "cadastrar_materia_prima", "consultar_disponibilidade", "consultar_produto", "registrar_venda"}:
            raise EstoqueError("INVALID_INPUT", "Operação de estoque inválida.")

        def execute():
            with self.app.app_context():
                return getattr(estoque_service, command)(**kwargs)

        result = await asyncio.to_thread(execute)
        if command == "registrar_venda":
            self.agendar_alertas({item["produto_id"] for item in kwargs["itens"]})
        elif command in {"movimentar", "configurar_minimo"} and kwargs.get("tipo_item") == "produto":
            self.agendar_alertas({kwargs["item_id"]})
        return result

    async def handle_request(self, sender, envelope):
        try:
            from app.agents.protocol import PREVISAO_PUBLICADA
            if envelope.ontology == PREVISAO_PUBLICADA:
                from app.agents.protocol import JIDS
                if sender != JIDS["previsao"]:
                    return failure(sender, envelope.ontology, envelope.thread, "UNSUPPORTED_ONTOLOGY",
                                   "Somente o agente de previsão pode anunciar uma publicação.")
                self.agendar_alertas()
                return encode(sender, envelope.ontology, {"status": "agendado"}, "inform", envelope.thread)
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

    async def consultar_risco(self, produto_id, granularidade="diaria", horizonte_dias=None, timeout=5):
        horizonte_dias = horizonte_preventivo(self.app, horizonte_dias)
        product = await self.executar("consultar_produto", produto_id=produto_id)
        stock = product["disponivel"]
        try:
            forecast = await self.request("previsao", PREVISAO, {"produto_id": produto_id,
                "granularidade": granularidade, "horizonte_dias": horizonte_dias}, timeout)
        except RemoteFailure as error:
            return {"produto_id": produto_id, "disponivel": stock, "status": "previsao_indisponivel",
                    "risco": None, "code": error.code, "mensagem": str(error)}
        if (forecast.payload["produto_id"] != produto_id
                or forecast.payload["granularidade"] != granularidade
                or forecast.payload["horizonte_dias"] != horizonte_dias):
            raise RemoteFailure({"code": "INVALID_RESPONSE", "message": "Previsão incompatível com a consulta."})
        if forecast.payload["cobertura_inicio"] != datetime.now(ZoneInfo("America/Manaus")).date().isoformat():
            return {**product, "status": "previsao_desatualizada", "risco": None,
                    "mensagem": "A cobertura da previsão não começa hoje; recalcule antes de avaliar o risco.",
                    "previsao": forecast.payload}
        # A comunicação pode ter coincidido com uma venda ou reposição.
        product = await self.executar("consultar_produto", produto_id=produto_id)
        stock = product["disponivel"]
        with localcontext() as context:
            context.prec = 128
            projected = Decimal(str(stock)) - Decimal(forecast.payload["total_previsto"])
            text = format(projected, "f")
            text = text.rstrip("0").rstrip(".") if "." in text else text
            return {**product, "saldo_projetado": text, "status": "ok",
                    "risco": projected < Decimal(str(product["estoque_minimo"])), "previsao": forecast.payload}

    async def sincronizar_alerta_preventivo(self, produto_id, horizonte_dias=None, timeout=5):
        from app.services.alerta_preventivo_service import sincronizar_alerta_preventivo

        horizon = horizonte_persistente(self.app, horizonte_dias)
        await self.executar("consultar_produto", produto_id=produto_id)
        try:
            response = await self.request("previsao", PREVISAO, {"produto_id": produto_id,
                "granularidade": "diaria", "horizonte_dias": horizon}, timeout)
        except (RemoteFailure, AgentUnavailable) as error:
            return {"produto_id": produto_id, "status": "previsao_indisponivel", "risco": None,
                    "code": getattr(error, "code", "FORECAST_UNAVAILABLE"),
                    "mensagem": "Não foi possível avaliar a previsão; os alertas anteriores foram preservados."}
        if self._alerts_stopped:
            raise AgentUnavailable("O agente de estoque está encerrando.")

        def save():
            with self.app.app_context():
                return sincronizar_alerta_preventivo(produto_id, response.payload, horizon,
                                                    cancel_event=self._alerts_cancel)

        return await asyncio.to_thread(save)

    async def stop(self):
        self._alerts_stopped = True
        self._alerts_cancel.set()
        if self._alert_task is not None:
            self._alert_task.cancel()
            await asyncio.gather(self._alert_task, return_exceptions=True)
        await super().stop()
