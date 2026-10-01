"""O serviço pode coordenar uma conversa sem instanciar o agente."""

from app.database import db
from app.models.contexto_conversa import ContextoConversa
from app.models.produto import Produto
from app.models.venda import Venda
from app.services.atendimento_service import AtendimentoService


def test_servico_recarrega_preco_e_contexto_entre_instancias(app, catalogo):
    with app.app_context():
        assert "Total: R$ 204.00" in AtendimentoService().responder(
            "  QUERO 2 CAIXAS DE 10L CHOCOLATE  ", cliente_nome="Cliente Simulado 2"
        )
        produto_id = catalogo["caixa de 10L - chocolate"]
        db.session.get(Produto, produto_id).preco = 110.0
        db.session.commit()
        db.session.remove()

    with app.app_context():
        assert AtendimentoService().responder("sim", cliente_nome="Cliente Simulado 2") == (
            "Venda registrada!\n- caixa de 10L - chocolate: 2 unidade(s) (R$ 220.00)\n"
            "Total do pedido: R$ 220.00"
        )
        assert ContextoConversa.query.count() == 0
        venda = Venda.query.one()
        assert (venda.produto_id, venda.cliente_nome, venda.quantidade) == (
            produto_id, "Cliente Simulado 2", 2
        )
        assert db.session.get(Produto, produto_id).quantidade_disponivel == 18
