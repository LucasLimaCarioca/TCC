"""Proteções adicionais para confirmação, cancelamento e isolamento por cliente."""

from app.database import db
from app.models.contexto_conversa import ContextoConversa
from app.models.historico_conversa import HistoricoConversa
from app.models.produto import Produto
from app.models.venda import Venda


def test_cancelamento_remove_contexto_sem_vender(app, conversar):
    conversar("quero 2 caixas de 10L chocolate")
    assert conversar("não") == "Pedido cancelado. Posso ajudar com outra coisa?"
    with app.app_context():
        assert ContextoConversa.query.count() == 0
        assert Venda.query.count() == 0
        assert all(p.quantidade_disponivel == 20 for p in Produto.query.all())


def test_repetir_confirmacao_nao_duplica_venda(app, catalogo, conversar):
    conversar("quero 2 caixas de 10L chocolate")
    conversar("sim")
    assert conversar("sim").startswith("Não encontrei nenhum pedido aguardando confirmação.")
    with app.app_context():
        assert Venda.query.count() == 1
        produto = db.session.get(Produto, catalogo["caixa de 10L - chocolate"])
        assert produto.quantidade_disponivel == 18


def test_contextos_e_historicos_separados_por_cliente(app, conversar, client):
    conversar("quero 2 caixas de 10L chocolate", "Cliente Simulado")
    conversar("quero 1 caixa de 5L morango", "Cliente Simulado 2")
    conversar("sim", "Cliente Simulado 2")
    with app.app_context():
        assert ContextoConversa.query.one().cliente_nome == "Cliente Simulado"
        assert Venda.query.one().cliente_nome == "Cliente Simulado 2"
        assert HistoricoConversa.query.filter_by(cliente_nome="Cliente Simulado").count() == 1
        assert HistoricoConversa.query.filter_by(cliente_nome="Cliente Simulado 2").count() == 2
    # O histórico persiste entre requests e a tela recupera apenas a conversa selecionada.
    pagina = client.get("/?cliente_id=1").get_data(as_text=True)
    assert "quero 2 caixas de 10L chocolate" in pagina
    assert "quero 1 caixa de 5L morango" not in pagina


def test_confirmacao_revalida_estoque(app, catalogo, conversar):
    conversar("quero 2 caixas de 10L chocolate")
    with app.app_context():
        db.session.get(Produto, catalogo["caixa de 10L - chocolate"]).quantidade_disponivel = 1
        db.session.commit()
    assert conversar("sim") == "No momento temos apenas 1 unidades de caixa de 10L - chocolate."
    with app.app_context():
        assert Venda.query.count() == 0
        assert db.session.get(Produto, catalogo["caixa de 10L - chocolate"]).quantidade_disponivel == 1
        # Caracterização: o protótipo descarta o contexto mesmo quando a confirmação falha.
        assert ContextoConversa.query.count() == 0
