"""Fachada compatível com as rotas do TCC I para o serviço de atendimento."""

from app.services.atendimento_service import AtendimentoService


class AtendimentoAgent:
    """Delega o atendimento síncrono; a integração SPADE pertence à Fase 3."""

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
