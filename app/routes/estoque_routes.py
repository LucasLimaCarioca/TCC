from flask import Blueprint, render_template

estoque_bp = Blueprint("estoque", __name__)

@estoque_bp.route("/estoque")
def tela_estoque():

    estoque = [
        {
            "nome": "Pizza Calabresa",
            "quantidade": 20,
            "minimo": 10
        },
        {
            "nome": "Hambúrguer",
            "quantidade": 5,
            "minimo": 10
        },
        {
            "nome": "Refrigerante",
            "quantidade": 30,
            "minimo": 15
        }
    ]

    return render_template(
        "estoque.html",
        titulo="Controle de Estoque",
        estoque=estoque
    )