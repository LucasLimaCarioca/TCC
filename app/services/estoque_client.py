"""Porta síncrona: serviço local ou gateway do agente SPADE ativo."""

from flask import current_app

from app.agents.base_agent import RemoteFailure
from app.database import db
from app.models.venda import Venda
from app.services import estoque_service
from app.services.estoque_service import EstoqueError


class EstoqueClient:
    def __init__(self, gateway=None):
        self.gateway = gateway

    def _gateway(self):
        return self.gateway or current_app.extensions.get("agent_gateway")

    def consultar_disponibilidade(self, itens):
        gateway = self._gateway()
        if gateway:
            try:
                return gateway.consultar_disponibilidade(itens).payload
            except RemoteFailure as error:
                if error.code not in {"PRODUCT_NOT_FOUND", "INVALID_QUANTITY", "INVALID_INPUT"}:
                    raise
                raise EstoqueError(error.code, str(error)) from None
        return estoque_service.consultar_disponibilidade(itens)

    def registrar_pedido(self, itens, cliente_nome="Cliente Simulado", operacao_id=None):
        try:
            grouped = estoque_service.agrupar_itens(itens)
            if not grouped:
                return True, "Venda registrada com sucesso.", []
            estoque_service.validar_cliente(cliente_nome)
            operacao_id = estoque_service.validar_operacao_id(operacao_id)
            gateway = self._gateway()
            if gateway:
                result = gateway.baixar_estoque(grouped, cliente_nome, operacao_id).payload
            else:
                result = estoque_service.registrar_venda(grouped, cliente_nome, operacao_id)
        except (EstoqueError, RemoteFailure) as error:
            if error.code not in {"INVALID_QUANTITY", "INVALID_INPUT", "PRODUCT_NOT_FOUND",
                                  "INSUFFICIENT_STOCK", "INVALID_OPERATION", "IDEMPOTENCY_CONFLICT"}:
                raise
            return False, str(error), []
        db.session.expire_all()
        vendas = [db.session.get(Venda, key) for key in result["vendas"]]
        return True, "Venda registrada com sucesso.", vendas

    def executar(self, command, **kwargs):
        gateway = self._gateway()
        if gateway:
            return gateway.executar_estoque(command, **kwargs)
        return getattr(estoque_service, command)(**kwargs)
