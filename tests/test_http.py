import pytest

from app.database import db
from app.models.historico_conversa import HistoricoConversa
from app.models.produto import Produto


@pytest.mark.parametrize("rota,titulo", [
    ("/", "Atendimento"), ("/estoque", "Controle de Estoque"), ("/vendas", "Vendas"),
])
def test_telas_principais(client, rota, titulo):
    response = client.get(rota)
    assert response.status_code == 200
    assert titulo in response.get_data(as_text=True)


def test_catalogo_redireciona_para_tela_principal_estoque(client):
    response = client.get("/produtos")
    assert response.status_code == 302
    assert response.headers["Location"] == "/estoque"
    page = client.get("/estoque")
    assert 'href="/estoque">Estoque</a>' in page.get_data(as_text=True)


@pytest.mark.parametrize("rota,chave_id", [("/api/produtos", "id"), ("/api/estoque", "produto_id")])
def test_catalogo_e_estoque_omitem_inativos(app, catalogo, client, rota, chave_id):
    inativo = catalogo["caixa de 10L - chocolate"]
    with app.app_context():
        db.session.get(Produto, inativo).ativo = False
        db.session.commit()
    response = client.get(rota)
    assert response.status_code == 200
    dados = response.get_json()
    assert len(dados) == 10
    assert {p[chave_id] for p in dados} == set(catalogo.values()) - {inativo}
    assert all(p["quantidade_disponivel"] == 20 for p in dados)


def test_api_venda_e_listagem(client, catalogo):
    produto_id = catalogo["caixa de 10L - chocolate"]
    response = client.post("/api/vendas", json={"produto_id": produto_id, "quantidade": 2})
    assert response.status_code == 201
    venda = response.get_json()["venda"]
    assert venda["produto_id"] == produto_id
    assert venda["quantidade"] == 2
    assert venda["valor_total"] == 204.0
    assert venda["cliente_nome"] == "Cliente Simulado"
    response = client.get("/api/vendas")
    assert response.status_code == 200
    vendas = response.get_json()
    assert len(vendas) == 1
    assert vendas[0]["id"] == venda["id"]
    assert vendas[0]["data_venda"]


def test_api_venda_rejeita_estoque_insuficiente(client, catalogo):
    response = client.post("/api/vendas", json={
        "produto_id": catalogo["caixa de 10L - chocolate"], "quantidade": 21,
    })
    assert response.status_code == 400
    assert response.get_json() == {
        "erro": "No momento temos apenas 20 unidades de caixa de 10L - chocolate.",
    }
    assert client.get("/api/vendas").get_json() == []


def test_api_atendimento_rejeita_mensagem_vazia(app, client):
    response = client.post("/api/atendimento", json={"mensagem": "   "})
    assert response.status_code == 400
    assert response.get_json() == {"erro": "Mensagem não informada."}
    with app.app_context():
        assert HistoricoConversa.query.count() == 0
