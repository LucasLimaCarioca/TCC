"""API compatível de vendas, delegando toda baixa à autoridade de estoque."""

from app.services.estoque_client import EstoqueClient


def registrar_venda(produto_id, quantidade, cliente_nome="Cliente Simulado", operacao_id=None):
    sucesso, mensagem, vendas = registrar_vendas_multiplas(
        [{"produto_id": produto_id, "quantidade": quantidade}], cliente_nome, operacao_id
    )
    return sucesso, mensagem, vendas[0] if vendas else None


def registrar_vendas_multiplas(itens, cliente_nome="Cliente Simulado", operacao_id=None):
    return EstoqueClient().registrar_pedido(itens, cliente_nome, operacao_id)
