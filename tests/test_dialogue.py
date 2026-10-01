"""Diálogo testável sem Flask, ORM, banco ou rede, com catálogo fictício."""

from dataclasses import dataclass

import pytest

from app.dialogue.intent_parser import IntentParser
from app.dialogue.order_parser import OrderParser
from app.dialogue.response_builder import ResponseBuilder


@dataclass(frozen=True)
class ProdutoDialogo:
    id: int
    nome: str
    categoria: str
    sabor: str
    preco: float = 12.5
    quantidade_disponivel: int = 20


@pytest.fixture
def produtos():
    return [
        ProdutoDialogo(1, "caixa de 10L - chocolate", "caixa de 10L", "chocolate"),
        ProdutoDialogo(2, "caixa de 10L - morango", "caixa de 10L", "morango"),
        ProdutoDialogo(3, "caixa de 5L - chocolate", "caixa de 5L", "chocolate"),
        ProdutoDialogo(4, "caixa de picole - caixa A", "caixa de picole", "caixa A"),
        ProdutoDialogo(5, "caixa de sundae - morango", "caixa de sundae", "morango"),
    ]


@pytest.mark.parametrize(("mensagem", "intencao"), [
    ("  OLÁ! Quero comprar  ", "saudacao"),
    ("chocolate", "desconhecida"),
    ("quero 2 caixas de 10L sabor chocolate", "consultar_sabores"),
    ("quais produtos tem?", "consultar_sabores"),
    ("quero saber o preço", "consultar_precos"),
    ("quero saber se tem 10l chocolate", "consultar_disponibilidade"),
    ("quero 10l chocolate", "registrar_venda"),
    ("confirmo!", "confirmacao_sem_contexto"),
])
def test_prioridade_e_normalizacao(mensagem, intencao):
    parser = IntentParser()
    normalizada = parser.normalizar(mensagem)
    assert normalizada == mensagem.lower().strip()
    assert parser.interpretar(normalizada) == intencao


def test_confirmacao_exige_palavras_completas():
    parser = IntentParser()
    assert not parser.afirmativa("simulado")
    assert not parser.negativa("cancelamento")
    assert parser.afirmativa("pode ser!")
    assert parser.negativa("não, obrigado")


@pytest.mark.parametrize(("termo", "produto_id"), [
    ("10 litros chocolate", 1),
    ("caixas de 10l chocolate", 1),
    ("5 litros chocolate", 3),
    ("picolé caixa a", 4),
    ("caixa picolé caixa a", 4),
    ("sundae morango", 5),
])
def test_aliases_preservam_produto_e_quantidade(produtos, termo, produto_id):
    itens = OrderParser().extrair_itens(f"quero 2 {termo}", produtos)
    assert [(i["produto"].id, i["quantidade"]) for i in itens] == [(produto_id, 2)]


def test_itens_seguem_ordem_da_frase_e_disponibilidade_a_do_catalogo(produtos):
    parser = OrderParser()
    mensagem = "quero 3 sundae morango e 2 caixas de 10l chocolate"
    itens = parser.extrair_itens(mensagem, produtos)
    assert [(i["produto"].id, i["quantidade"]) for i in itens] == [(5, 3), (1, 2)]
    assert parser.extrair_produtos(mensagem, produtos) == [produtos[4], produtos[0]]
    assert parser.encontrar_produto(mensagem, produtos) == produtos[0]


def test_categoria_compartilhada_e_primeira_ocorrencia(produtos):
    parser = OrderParser()
    itens = parser.extrair_itens("quero 1 caixa de 10l de chocolate e morango", produtos)
    assert {(i["produto"].id, i["quantidade"]) for i in itens} == {(1, 1), (2, 1)}
    itens = parser.extrair_itens("quero 2 sundae morango e 3 sundae morango", produtos)
    assert [(i["produto"].id, i["quantidade"]) for i in itens] == [(5, 2)]


def test_ambiguidade_e_catalogo_vazio(produtos):
    parser = OrderParser()
    assert parser.encontrar_ambiguidade("quero chocolate", produtos) == (
        "chocolate", [produtos[0], produtos[2]]
    )
    assert parser.encontrar_ambiguidade("quero manga", produtos) is None
    assert parser.extrair_itens("quero chocolate", []) == []
    assert parser.encontrar_produto("tem chocolate?", []) is None


def test_resumo_e_totais_sem_objetos_orm(produtos):
    itens = [
        {"produto": produtos[0], "quantidade": 2},
        {"produto": produtos[4], "quantidade": 3},
    ]
    resposta = ResponseBuilder().confirmacao_pedido(itens)
    assert resposta == (
        "Encontrei este pedido:\n"
        "- caixa de 10L - chocolate: 2 unidade(s) (R$ 25.00)\n"
        "- caixa de sundae - morango: 3 unidade(s) (R$ 37.50)\n"
        "Total: R$ 62.50\nConfirma a compra? Responda sim ou não."
    )
