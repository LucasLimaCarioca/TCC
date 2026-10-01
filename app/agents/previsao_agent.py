"""Agente SPADE de Previsão: infraestrutura; previsão nas fases seguintes."""

from app.agents.base_agent import BaseAgent


class PrevisaoAgent(BaseAgent):
    def __init__(self, password, port=5222, audit=None):
        super().__init__("previsao", password, port, audit)
