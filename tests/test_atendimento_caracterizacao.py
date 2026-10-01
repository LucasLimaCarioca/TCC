"""Contratos do protótipo registrados antes da extração da Fase 2."""

import json

import pytest

from app.agents.atendimento_agent import AtendimentoAgent
from app.database import db
from app.models.contexto_conversa import ContextoConversa
from app.models.produto import Produto
from app.models.venda import Venda


@pytest.mark.parametrize(("mensagem", "resposta"), [
    ("OLÁ!", "Olá! Sou o agente de atendimento da sorveteria.\n"
     "Posso informar sabores, preços, disponibilidade ou registrar um pedido."),
    ("chocolate", "Desculpe, não entendi sua solicitação.\n"
     "Você pode perguntar por sabores, preços, disponibilidade ou fazer um pedido."),
    ("sim", "Não encontrei nenhum pedido aguardando confirmação.\n"
     "Para iniciar um pedido, envie algo como: quero 2 caixas de 10L chocolate."),
    ("quero manga", "Não consegui identificar os produtos do pedido.\n"
     "Exemplo: quero 2 caixas de 10L chocolate e 1 caixa de sundae morango."),
    ("quero chocolate", "Encontrei o sabor chocolate em mais de uma categoria: "
     "caixa de 10L, caixa de 5L, caixa de sundae.\n"
     "Informe também a categoria/tamanho.\nExemplo: quero 2 caixas de 10L chocolate."),
])
def test_respostas_sem_contexto(conversar, mensagem, resposta):
    assert conversar(mensagem) == resposta


def test_lembrete_preserva_pedido(conversar, app):
    conversar("quero 2 caixas de 10L chocolate")
    assert conversar("preços") == (
        "Ainda tenho um pedido aguardando confirmação:\n"
        "- caixa de 10L - chocolate: 2 unidade(s) (R$ 204.00)\n"
        "Total: R$ 204.00\nResponda sim para confirmar ou não para cancelar."
    )
    with app.app_context():
        assert ContextoConversa.query.count() == 1
        assert Venda.query.count() == 0


def test_cancelamento_tem_prioridade_sobre_confirmacao(conversar, app):
    conversar("quero 2 caixas de 10L chocolate")
    assert conversar("sim, não") == "Pedido cancelado. Posso ajudar com outra coisa?"
    with app.app_context():
        assert ContextoConversa.query.count() == 0
        assert Venda.query.count() == 0


def test_novo_pedido_substitui_contexto(conversar, app, catalogo):
    conversar("quero 2 caixas de 10L chocolate")
    conversar("quero 3 caixas de 5L morango")
    with app.app_context():
        contexto = ContextoConversa.query.one()
        assert json.loads(contexto.itens_json) == [
            {"produto_id": catalogo["caixa de 5L - morango"], "quantidade": 3}
        ]
    assert conversar("sim") == (
        "Venda registrada!\n- caixa de 5L - morango: 3 unidade(s) (R$ 198.00)\n"
        "Total do pedido: R$ 198.00"
    )
    with app.app_context():
        assert Venda.query.one().produto_id == catalogo["caixa de 5L - morango"]


def test_novo_pedido_invalido_mantem_anterior(conversar, app):
    conversar("quero 2 caixas de 10L chocolate")
    assert conversar("quero 21 caixas de 5L morango").startswith("No momento temos apenas")
    assert "Total do pedido: R$ 204.00" in conversar("sim")
    with app.app_context():
        assert Venda.query.one().quantidade == 2


def test_contexto_legado_compartilhado_entre_instancias(app, catalogo):
    with app.app_context():
        db.session.add(ContextoConversa(
            cliente_nome="Cliente Simulado", etapa="aguardando_confirmacao",
            produto_id=catalogo["caixa de 10L - chocolate"], quantidade=2,
        ))
        db.session.commit()
        assert "Total: R$ 204.00" in AtendimentoAgent().responder("oi")
        assert AtendimentoAgent().responder("sim") == (
            "Venda registrada!\n- caixa de 10L - chocolate: 2 unidade(s) (R$ 204.00)\n"
            "Total do pedido: R$ 204.00"
        )
        assert ContextoConversa.query.count() == 0
        assert Venda.query.one().quantidade == 2


def test_quantidade_omitida_assume_um(conversar):
    assert conversar("quero sundae morango") == (
        "Encontrei este pedido:\n- caixa de sundae - morango: 1 unidade(s) (R$ 45.00)\n"
        "Total: R$ 45.00\nConfirma a compra? Responda sim ou não."
    )


def test_zero_so_e_rejeitado_na_confirmacao(conversar, app):
    assert "0 unidade(s)" in conversar("quero 0 caixas de 10L chocolate")
    assert conversar("sim") == "Informe uma quantidade maior que zero."
    with app.app_context():
        assert Venda.query.count() == 0
        assert ContextoConversa.query.count() == 0


def test_palavra_sabor_tem_prioridade_sobre_pedido(conversar):
    assert conversar("quero 2 caixas de 10L sabor chocolate") == conversar("sabores")


@pytest.mark.parametrize(("mensagem", "resposta"), [
    ("sabores", "No momento não há sabores cadastrados."),
    ("preços", "No momento não há produtos cadastrados."),
    ("estoque", "No momento não há produtos ativos para consulta."),
])
def test_catalogo_sem_produtos_ativos(app, conversar, mensagem, resposta):
    with app.app_context():
        for produto in Produto.query.all():
            produto.ativo = False
        db.session.commit()
    assert conversar(mensagem) == resposta


def test_preco_mais_de_tres_produtos_retorna_tabela(conversar):
    assert conversar(
        "preço 10l chocolate, 10l morango, 5l chocolate e 5l morango"
    ) == conversar("tabela")


def test_disponibilidade_zerada(app, catalogo, conversar):
    with app.app_context():
        db.session.get(Produto, catalogo["caixa de 10L - chocolate"]).quantidade_disponivel = 0
        db.session.commit()
    assert conversar("tem 10l chocolate?") == (
        "No momento não temos caixa de 10L - chocolate em estoque."
    )


def test_metodos_publicos_de_venda(app, catalogo):
    with app.app_context():
        agent = AtendimentoAgent()
        assert agent.registrar_venda("inexistente", 1) == "Produto não encontrado."
        assert agent.registrar_venda("caixa de 10L - chocolate", 2) == (
            "Venda registrada!\nProduto: caixa de 10L - chocolate\n"
            "Quantidade: 2\nTotal: R$ 204.0"
        )
        assert agent.registrar_pedido([
            {"produto_id": catalogo["caixa de 5L - morango"], "quantidade": 1}
        ], cliente_nome="Cliente Simulado 2") == (
            "Venda registrada!\n- caixa de 5L - morango: 1 unidade(s) (R$ 66.00)\n"
            "Total do pedido: R$ 66.00"
        )
        assert Venda.query.filter_by(cliente_nome="Cliente Simulado 2").one().quantidade == 1
