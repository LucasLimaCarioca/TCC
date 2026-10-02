"""Agente SPADE de Atendimento e fachada local compatível com o TCC I."""

import asyncio

from app.services.atendimento_service import AtendimentoService
from app.services.estoque_client import EstoqueClient
from app.agents.base_agent import AgentUnavailable, BaseAgent


class AtendimentoSPADEAgent(BaseAgent):
    """Agente real, com serviço executado em worker e contexto Flask próprio."""

    def __init__(self, password, app, port=5222, audit=None, gateway=None):
        super().__init__("atendimento", password, port, audit)
        self.app = app
        self.gateway = gateway

    async def responder(self, mensagem, cliente_nome="Cliente Simulado"):
        if not self.is_alive() or not self.client.is_connected():
            raise AgentUnavailable("Agente de atendimento desconectado.")

        def execute():
            with self.app.app_context():
                return AtendimentoService(EstoqueClient(self.gateway)).responder(mensagem, cliente_nome=cliente_nome)

        # Nenhuma sessão ORM ou contexto Flask atravessa a ponte entre threads.
        return await asyncio.to_thread(execute)


class AtendimentoAgent:
    """Fachada local compatível usada quando o runtime está desativado."""

    def __init__(self):
        self.service = AtendimentoService()

    def responder(self, mensagem, cliente_nome="Cliente Simulado"):
        return self.service.responder(mensagem, cliente_nome=cliente_nome)

    def registrar_venda(self, produto_nome, quantidade, cliente_nome="Cliente Simulado"):
        return self.service.registrar_venda(
            produto_nome, quantidade, cliente_nome=cliente_nome
        )

    def registrar_pedido(self, itens, cliente_nome="Cliente Simulado"):
        return self.service.registrar_pedido(itens, cliente_nome=cliente_nome)
