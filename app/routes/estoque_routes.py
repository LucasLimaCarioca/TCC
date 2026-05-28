from flask import Blueprint, jsonify, redirect, url_for

from app.models.produto import Produto


estoque_bp = Blueprint("estoque", __name__)


@estoque_bp.route("/estoque")
def tela_estoque():
    # A tela visual foi unificada em Produtos e Estoque.
    # Mantemos esta rota para nao quebrar links antigos.
    return redirect(url_for("produto.tela_produtos"))


@estoque_bp.route("/api/estoque")
def consultar_estoque_api():
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(Produto.nome).all()

    return jsonify([
        {
            "produto_id": produto.id,
            "nome": produto.nome,
            "quantidade_disponivel": produto.quantidade_disponivel
        }
        for produto in produtos
    ])
