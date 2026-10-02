"""Regras da Fase 4 em banco temporário e com dados exclusivamente fictícios."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.agents.base_agent import AgentTimeout
from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.contexto_conversa import ContextoConversa
from app.models.materia_prima import MateriaPrima
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.operacao_estoque import OperacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.services.atendimento_service import AtendimentoService
from app.services.estoque_client import EstoqueClient
from app.services import estoque_service as estoque
from app.services.venda_service import registrar_vendas_multiplas


def test_itens_repetidos_somam_antes_de_validar(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        items = [{"produto_id": product, "quantidade": 12}] * 2
        assert estoque.consultar_disponibilidade(items) == {"available": False, "items": [
            {"produto_id": product, "solicitado": 24, "disponivel": 20, "ok": False}]}
        assert registrar_vendas_multiplas(items)[0] is False
        assert db.session.get(Produto, product).quantidade_disponivel == 20
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == OperacaoEstoque.query.count() == 0


def test_venda_agrupada_receipt_e_movimentacao_unica(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    key = str(uuid4())
    with app.app_context():
        items = [{"produto_id": product, "quantidade": 2}, {"produto_id": product, "quantidade": 3}]
        result = estoque.registrar_venda(items, operacao_id=key)
        db.session.remove()
        assert estoque.registrar_venda(items, operacao_id=key) == result
        sale = Venda.query.one()
        movement = MovimentacaoEstoque.query.one()
        assert sale.quantidade == movement.quantidade == 5
        assert sale.valor_total == 510
        assert movement.venda_id == sale.id == result["vendas"][0]
        assert movement.tipo_movimentacao == "venda"
        assert (movement.saldo_anterior, movement.saldo_posterior) == (20, 15)
        assert db.session.get(Produto, product).quantidade_disponivel == 15
        assert OperacaoEstoque.query.one().id == key
        with pytest.raises(estoque.EstoqueError) as error:
            estoque.registrar_venda([{ "produto_id": product, "quantidade": 1}], operacao_id=key)
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
        assert Venda.query.count() == 1


def test_erro_ao_gravar_movimento_reverte_venda_saldo_alerta_e_receipt(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]

    def fail(session, *_):
        if any(isinstance(obj, MovimentacaoEstoque) for obj in session.new):
            raise RuntimeError("Falha fictícia de armazenamento")

    with app.app_context():
        event.listen(Session, "before_flush", fail)
        try:
            with pytest.raises(RuntimeError):
                estoque.registrar_venda([{"produto_id": product, "quantidade": 20}])
        finally:
            event.remove(Session, "before_flush", fail)
        db.session.remove()
        assert db.session.get(Produto, product).quantidade_disponivel == 20
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == AlertaEstoque.query.count() == OperacaoEstoque.query.count() == 0


def test_vendas_concorrentes_nao_usam_mesmo_saldo(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]

    def sell(_):
        with app.app_context():
            try:
                estoque.registrar_venda([{"produto_id": product, "quantidade": 7}])
                return True
            except estoque.EstoqueError as error:
                assert error.code == "INSUFFICIENT_STOCK"
                return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(sell, range(4)))
    assert results.count(True) == 2
    with app.app_context():
        assert db.session.get(Produto, product).quantidade_disponivel == 6
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == OperacaoEstoque.query.count() == 2


def test_repeticoes_concorrentes_mesma_chave_sao_uma_venda(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    key = str(uuid4())

    def sell(_):
        with app.app_context():
            return estoque.registrar_venda([{"produto_id": product, "quantidade": 2}], operacao_id=key)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(sell, range(4)))
    assert all(result == results[0] for result in results)
    with app.app_context():
        assert db.session.get(Produto, product).quantidade_disponivel == 18
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == OperacaoEstoque.query.count() == 1


def test_resposta_perdida_mantem_contexto_para_repeticao_segura(app, catalogo):
    class LostReply(EstoqueClient):
        first = True

        def registrar_pedido(self, *args, **kwargs):
            result = super().registrar_pedido(*args, **kwargs)
            if self.first:
                self.first = False
                raise AgentTimeout("Resposta fictícia perdida após commit")
            return result

    with app.app_context():
        port = LostReply()
        service = AtendimentoService(port)
        service.responder("quero 2 caixas de 10L chocolate")
        key = ContextoConversa.query.one().operacao_id
        with pytest.raises(AgentTimeout):
            service.responder("sim")
        db.session.remove()
        assert ContextoConversa.query.one().operacao_id == key
        assert Venda.query.count() == 1
        assert "Venda registrada!" in AtendimentoService(port).responder("sim")
        assert ContextoConversa.query.count() == 0
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 1
        assert db.session.get(Produto, catalogo["caixa de 10L - chocolate"]).quantidade_disponivel == 18


def test_entrada_saida_ajuste_final_e_ajuste_igual(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        assert estoque.movimentar("produto", product, "entrada", 3, "Reposição fictícia")["saldo"] == "23"
        assert estoque.movimentar("produto", product, "saida", 2, "Avaria fictícia")["saldo"] == "21"
        key = str(uuid4())
        result = estoque.movimentar("produto", product, "ajuste", 8, "Contagem fictícia", key)
        assert result["saldo"] == "8"
        assert estoque.movimentar("produto", product, "ajuste", 8, "Contagem fictícia", key) == result
        result = estoque.movimentar("produto", product, "ajuste", 8, "Sem diferença")
        assert result == {"alterado": False, "movimentacao_id": None, "saldo": "8"}
        movements = MovimentacaoEstoque.query.order_by(MovimentacaoEstoque.id).all()
        assert [(m.tipo_movimentacao, m.quantidade, m.saldo_anterior, m.saldo_posterior) for m in movements] == [
            ("entrada", 3, 20, 23), ("saida", 2, 23, 21), ("ajuste", 13, 21, 8)]
        with pytest.raises(estoque.EstoqueError):
            estoque.movimentar("produto", product, "saida", 9, "Avaria fictícia")
        assert db.session.get(Produto, product).quantidade_disponivel == 8
        assert MovimentacaoEstoque.query.count() == 3


def test_alerta_igual_minimo_sem_duplicatas_resolve_e_reabre(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        estoque.configurar_minimo("produto", product, 20)
        estoque.visao_estoque()
        estoque.visao_estoque()
        first = AlertaEstoque.query.one()
        assert first.ativo and first.tipo_alerta == "estoque_minimo"
        estoque.movimentar("produto", product, "entrada", 1, "Reposição fictícia")
        assert not first.ativo and first.resolvido_em
        estoque.movimentar("produto", product, "saida", 1, "Saída fictícia")
        assert AlertaEstoque.query.filter_by(ativo=True).count() == 1
        assert AlertaEstoque.query.count() == 2
        estoque.configurar_minimo("produto", product, 19)
        assert AlertaEstoque.query.filter_by(ativo=True).count() == 0


def test_materia_prima_decimal_e_sem_baixa_por_venda(app, catalogo):
    with app.app_context():
        material = estoque.cadastrar_materia_prima("MP-FICTICIA", "Insumo fictício", "kg", "1.25")["id"]
        estoque.movimentar("materia_prima", material, "entrada", "2.5", "Reposição fictícia")
        estoque.movimentar("materia_prima", material, "saida", "1.25", "Uso manual fictício")
        assert AlertaEstoque.query.filter_by(materia_prima_id=material, ativo=True).count() == 1
        estoque.registrar_venda([{"produto_id": catalogo["caixa de 10L - chocolate"], "quantidade": 1}])
        assert db.session.get(MateriaPrima, material).quantidade_disponivel == Decimal("1.25")
        assert MovimentacaoEstoque.query.filter_by(materia_prima_id=material).count() == 2
        estoque.movimentar("materia_prima", material, "ajuste", "0.5", "Contagem fictícia")
        assert db.session.get(MateriaPrima, material).quantidade_disponivel == Decimal("0.5")


@pytest.mark.parametrize("quantidade", [True, 1.5, "2", -1, None])
def test_quantidades_produto_invalidas_nao_movimentam(app, catalogo, quantidade):
    with app.app_context(), pytest.raises(estoque.EstoqueError):
        estoque.movimentar("produto", catalogo["caixa de 10L - chocolate"], "entrada", quantidade, "Teste fictício")
    with app.app_context():
        assert MovimentacaoEstoque.query.count() == 0


@pytest.mark.parametrize("quantidade", [True, 1.5, "NaN", "Infinity", "-1", "0.1234567891", "1e16"])
def test_decimal_invalido_nao_movimenta(app, quantidade):
    with app.app_context(), pytest.raises(estoque.EstoqueError):
        estoque.movimentar("materia_prima", 1, "entrada", quantidade, "Teste fictício")


def test_minimo_zero_alerta_com_saldo_zero_e_inativo_resolve(app, catalogo):
    product = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        estoque.movimentar("produto", product, "ajuste", 0, "Contagem fictícia")
        assert AlertaEstoque.query.filter_by(ativo=True).count() == 1
        db.session.get(Produto, product).ativo = False
        db.session.commit()
        assert estoque.visao_estoque()["alertas"][0]["ativo"] is False
