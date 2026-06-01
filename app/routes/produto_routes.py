from flask import Blueprint, jsonify, render_template

from app.models.produto import Produto


produto_bp = Blueprint("produto", __name__)


def produto_para_dict(produto):
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
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(
        Produto.categoria,
        Produto.sabor
    ).all()

    return render_template(
        "produtos.html",
        titulo="Produtos e Estoque",
        produtos=produtos
    )


@produto_bp.route("/api/produtos")
def listar_produtos_api():
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(
        Produto.categoria,
        Produto.sabor
    ).all()

    return jsonify([
        produto_para_dict(produto)
        for produto in produtos
    ])
