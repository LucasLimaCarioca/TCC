from flask import Blueprint, render_template

from app.models.produto import Produto


estoque_bp = Blueprint("estoque", __name__)


@estoque_bp.route("/estoque")
def tela_estoque():
    # Fase 2: o estoque simplificado fica dentro da tabela produtos.
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(Produto.nome).all()

    estoque = []

    for produto in produtos:
        estoque.append({
            "nome": produto.nome,
            "quantidade": produto.quantidade_disponivel,
            "minimo": 5
        })

    return render_template(
        "estoque.html",
        titulo="Controle de Estoque",
        estoque=estoque
    )
