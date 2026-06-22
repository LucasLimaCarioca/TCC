from flask import Blueprint, jsonify, redirect, url_for

from app.models.produto import Produto


# Blueprint das rotas ligadas à visão de estoque.
# A tela visual foi unificada com produtos, mas a API de estoque continua separada.
estoque_bp = Blueprint("estoque", __name__)


@estoque_bp.route("/estoque")
def tela_estoque():
    # A tela visual foi unificada em Produtos e Estoque.
    # Mantemos esta rota para nao quebrar links antigos.
    return redirect(url_for("produto.tela_produtos"))


@estoque_bp.route("/api/estoque")
def consultar_estoque_api():
    # Consulta somente produtos ativos, pois produtos inativos ficam no banco
    # apenas para preservar histórico de vendas/conversas antigas.
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(Produto.nome).all()

    # Retorna apenas os dados necessários para uma visão de estoque.
    return jsonify([
        {
            "produto_id": produto.id,
            "nome": produto.nome,
            "categoria": produto.categoria,
            "sabor": produto.sabor,
            "quantidade_disponivel": produto.quantidade_disponivel
        }
        for produto in produtos
    ])
