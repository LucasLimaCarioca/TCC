"""Saldos internos nunca se tornam mensagens de cliente; dados fictícios."""

from types import SimpleNamespace

import pytest

from app.agents.base_agent import RemoteFailure
from app.database import db
from app.dialogue.response_builder import ResponseBuilder
from app.models.contexto_conversa import ContextoConversa
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.services.atendimento_service import AtendimentoService
from app.services.estoque_client import EstoqueClient
from test_agent_runtime import runtime


@pytest.mark.parametrize("balance,expected", [(137, "disponível"), (0, "indisponível")])
def test_lista_usa_saldo_para_status_sem_exibir_quantidade(balance, expected):
    product = SimpleNamespace(id=1, nome="Produto fictício", categoria="Categoria fictícia",
                              sabor="Sabor fictício", quantidade_disponivel=999)
    response = ResponseBuilder().disponibilidade([product], saldos={1: balance})
    assert response == f"Disponibilidade atual:\nCategoria fictícia - Sabor fictício: {expected}\n"
    assert str(balance) not in response and "999" not in response


def test_api_disponibilidade_e_pedido_rejeitado_nao_expoem_saldo(app, catalogo, conversar):
    identifier = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        db.session.get(Produto, identifier).quantidade_disponivel = 137
        db.session.commit()
        internal = EstoqueClient().consultar_disponibilidade([{"produto_id": identifier, "quantidade": 1}])
        assert internal["items"][0]["disponivel"] == 137
    available = conversar("tem 10l chocolate?")
    assert available == "Sim, temos caixa de 10L - chocolate disponível."
    general = conversar("estoque")
    rejected = conversar("quero 138 caixas de 10L chocolate")
    assert rejected == ("A quantidade solicitada de caixa de 10L - chocolate não está disponível.\n"
                        "Você pode pedir uma quantidade menor ou escolher outro produto.")
    assert all("137" not in response for response in (available, general, rejected))
    with app.app_context():
        assert Venda.query.count() == ContextoConversa.query.count() == 0
        assert db.session.get(Produto, identifier).quantidade_disponivel == 137


def test_confirmacao_local_rejeita_sem_saldo_e_repete_com_mesma_chave(app, catalogo, conversar):
    identifier = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        db.session.get(Produto, identifier).quantidade_disponivel = 137
        db.session.commit()
    assert "20 unidade(s)" in conversar("quero 20 caixas de 10L chocolate")
    with app.app_context():
        operation = ContextoConversa.query.one().operacao_id
        db.session.get(Produto, identifier).quantidade_disponivel = 19
        db.session.commit()
    response = conversar("sim")
    assert response.startswith("Não há disponibilidade para a quantidade solicitada.")
    assert "quantidade menor" in response and "outro produto" in response and "pedido continua pendente" in response
    assert "19" not in response and "137" not in response
    with app.app_context():
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 0
        assert ContextoConversa.query.one().operacao_id == operation
        db.session.get(Produto, identifier).quantidade_disponivel = 137
        db.session.commit()
    receipt = conversar("sim")
    assert "20 unidade(s)" in receipt and "R$ 2040.00" in receipt
    with app.app_context():
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 1
        assert ContextoConversa.query.count() == 0
        assert db.session.get(Produto, identifier).quantidade_disponivel == 117


@pytest.mark.parametrize("method", ["registrar_pedido", "registrar_venda", "contexto_legado"])
def test_falha_remota_tipificada_nao_repete_mensagem_com_saldo(app, catalogo, method):
    identifier = catalogo["caixa de 10L - chocolate"]
    class RejectingGateway:
        def baixar_estoque(self, *args):
            raise RemoteFailure({"code": "INSUFFICIENT_STOCK",
                "message": "No momento temos apenas 863 unidades do produto fictício."})
    service = AtendimentoService(EstoqueClient(RejectingGateway()))
    with app.app_context():
        if method == "registrar_pedido":
            response = service.registrar_pedido([{"produto_id": identifier, "quantidade": 2}])
        elif method == "registrar_venda":
            response = service.registrar_venda("caixa de 10L - chocolate", 2)
        else:
            db.session.add(ContextoConversa(cliente_nome="Cliente Simulado", etapa="aguardando_confirmacao",
                                           produto_id=identifier, quantidade=2))
            db.session.commit()
            response = service.responder("sim")
            context = ContextoConversa.query.one()
            assert context.operacao_id is not None and context.quantidade == 2
        assert "863" not in response and "apenas" not in response
        assert "Não há disponibilidade para a quantidade solicitada." in response
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 0


def test_cliente_interno_preserva_erro_detalhado_por_padrao(app, catalogo):
    identifier = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        legacy = EstoqueClient().registrar_pedido([{"produto_id": identifier, "quantidade": 21}])
        private = EstoqueClient().registrar_pedido(
            [{"produto_id": identifier, "quantidade": 21}], ocultar_saldo=True)
        assert legacy[0] is private[0] is False
        assert "20 unidades" in legacy[1]
        assert "20" not in private[1] and "quantidade menor" in private[1]


def test_disponibilidade_e_race_de_confirmacao_por_xmpp_real(runtime, app, catalogo):
    identifier = catalogo["caixa de 10L - chocolate"]
    from app.agents.gateway import AgentGateway
    gateway = AgentGateway(runtime)
    with app.app_context():
        db.session.get(Produto, identifier).quantidade_disponivel = 137
        db.session.commit()
    assert gateway.responder("tem 10l chocolate?") == "Sim, temos caixa de 10L - chocolate disponível."
    assert "20 unidade(s)" in gateway.responder("quero 20 caixas de 10L chocolate")
    with app.app_context():
        db.session.get(Produto, identifier).quantidade_disponivel = 19
        db.session.commit()
    rejected = gateway.responder("sim")
    assert "19" not in rejected and "137" not in rejected
    assert "quantidade menor" in rejected and "pedido continua pendente" in rejected
    with app.app_context():
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 0
        assert ContextoConversa.query.one().quantidade == 20


@pytest.mark.parametrize("original,expected", [
    ("Sim, temos 137 unidades de Produto fictício de 10L em estoque.",
     "Sim, temos Produto fictício de 10L disponível."),
    ("No momento temos apenas 137 unidades de Produto fictício de 10L.\n"
     "O pedido continua pendente. Você pode enviar outro pedido ou responder não para cancelar.",
     "A quantidade solicitada de Produto fictício de 10L não está disponível.\n"
     "O pedido continua pendente. Você pode enviar outro pedido ou responder não para cancelar."),
    ("Disponibilidade atual:\nCategoria de 10L - Chocolate: 137 unidades\n"
     "Categoria de 5L - Morango: 0 unidades\n",
     "Disponibilidade atual:\nCategoria de 10L - Chocolate: disponível\n"
     "Categoria de 5L - Morango: indisponível\n"),
])
def test_historico_antigo_e_redigido_na_saida_sem_alterar_registro(app, client, original, expected):
    from app.models.historico_conversa import HistoricoConversa
    with app.app_context():
        row = HistoricoConversa(cliente_nome="Cliente Simulado", mensagem_usuario="quero 7 caixas de 10L",
                                resposta_agente=original)
        db.session.add(row)
        db.session.commit()
        identifier = row.id
    response = client.get("/")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "137" not in text and "quero 7 caixas de 10L" in text
    for line in expected.strip().splitlines():
        assert line in text
    with app.app_context():
        assert db.session.get(HistoricoConversa, identifier).resposta_agente == original


@pytest.mark.parametrize("text", [
    "O valor de Produto fictício de 10L é R$ 137.00.",
    "Venda registrada!\n- Produto fictício de 10L: 2 unidade(s) (R$ 274.00)\nTotal do pedido: R$ 274.00",
    "Encontrei este pedido:\n- Produto fictício: 137 unidades\nTotal: R$ 1.00",
    "Disponibilidade atual:\nCategoria de 10L - Chocolate: R$ 137.00\n",
])
def test_redacao_historica_nao_altera_precos_embalagens_ou_quantidades_do_pedido(text):
    assert ResponseBuilder.redigir_saldos_historicos(text) == text


