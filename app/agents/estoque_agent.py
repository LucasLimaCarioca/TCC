"""Agente SPADE de Estoque: infraestrutura; operações de estoque na Fase 4."""

from app.agents.base_agent import BaseAgent


class EstoqueAgent(BaseAgent):
    def __init__(self, password, port=5222, audit=None):
        super().__init__("estoque", password, port, audit)
