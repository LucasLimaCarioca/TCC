from flask import Blueprint, jsonify, render_template

from app.models.produto import Produto


# Blueprint das rotas de catálogo de produtos.
# Inclui a tela HTML e o endpoint JSON usado para testes/integrações.
produto_bp = Blueprint("produto", __name__)


def produto_para_dict(produto):
    # Converte um objeto Produto em dicionário para resposta JSON.
    # Objetos SQLAlchemy não podem ser enviados diretamente pelo jsonify.
    return {
        "id": produto.id,
        "nome": produto.nome,
        "categoria": produto.categoria,
        "sabor": produto.sabor,
        "preco": produto.preco,
        "descricao": produto.descricao,
        "quantidade_disponivel": produto.quantidade_disponivel,
        "ativo": produto.ativo
    }


@produto_bp.route("/produtos")
def tela_produtos():
    # A tela mostra apenas produtos ativos.
    # Produtos antigos/inativos podem continuar no banco para preservar histórico.
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(
        Produto.categoria,
        Produto.sabor
    ).all()

    # Renderiza a tela unificada de Produtos e Estoque.
    return render_template(
        "produtos.html",
        titulo="Produtos e Estoque",
        produtos=produtos
    )


@produto_bp.route("/api/produtos")
def listar_produtos_api():
    # Endpoint para consultar o catálogo sem interface gráfica.
    # A ordenação por categoria e sabor facilita conferir produtos com mesmo sabor.
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(
        Produto.categoria,
        Produto.sabor
    ).all()

    # Retorna uma lista JSON com todos os produtos ativos.
    return jsonify([
        produto_para_dict(produto)
        for produto in produtos
    ])
