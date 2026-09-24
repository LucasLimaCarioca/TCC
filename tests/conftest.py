"""Dados controlados do TCC I; nunca importa os scripts que alteram o banco local."""

import pytest

from app.app import create_app
from app.database import db
from app.models.cliente import Cliente
from app.models.produto import Produto


@pytest.fixture
def app(tmp_path):
    application = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'teste.db'}",
    })
    with application.app_context():
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def catalogo(app):
    # Mesmos 11 produtos, preços e saldos iniciais de seed.py, sem executá-lo.
    dados = [
        ("caixa de 10L", "morango", 101.0),
        ("caixa de 10L", "chocolate", 102.0),
        ("caixa de 10L", "baunilha", 100.0),
        ("caixa de 5L", "morango", 66.0),
        ("caixa de 5L", "chocolate", 67.0),
        ("caixa de 5L", "baunilha", 65.0),
        ("caixa de sundae", "morango", 45.0),
        ("caixa de sundae", "chocolate", 48.0),
        ("caixa de picole", "caixa A", 120.0),
        ("caixa de picole", "caixa B", 120.0),
        ("caixa de picole", "caixa C", 120.0),
    ]
    with app.app_context():
        produtos = [
            Produto(
                nome=f"{categoria} - {sabor}", categoria=categoria, sabor=sabor,
                preco=preco, quantidade_disponivel=20, ativo=True,
            )
            for categoria, sabor, preco in dados
        ]
        db.session.add_all(produtos)
        db.session.add_all([
            Cliente(nome="Cliente Simulado" if i == 1 else f"Cliente Simulado {i}")
            for i in range(1, 6)
        ])
        db.session.commit()
        return {produto.nome: produto.id for produto in produtos}


@pytest.fixture
def client(app, catalogo):
    return app.test_client()


@pytest.fixture
def conversar(client):
    def enviar(mensagem, cliente_nome="Cliente Simulado"):
        response = client.post("/api/atendimento", json={
            "mensagem": mensagem, "cliente_nome": cliente_nome,
        })
        assert response.status_code == 200
        dados = response.get_json()
        assert dados["cliente_nome"] == cliente_nome
        assert dados["mensagem_usuario"] == mensagem
        return dados["resposta_agente"]

    return enviar
