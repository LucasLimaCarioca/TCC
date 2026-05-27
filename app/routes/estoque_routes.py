from flask import Blueprint, render_template


estoque_bp = Blueprint("estoque", __name__)


@estoque_bp.route("/estoque")
def tela_estoque():
    # Etapa 1: a tela ainda usa dados fixos para validar a interface e a navegacao.
    # Nas proximas etapas, essa lista sera substituida por consultas ao banco SQLite.
    estoque = [
        {
            "nome": "Sorvete de Chocolate",
            "quantidade": 20,
            "minimo": 10
        },
        {
            "nome": "Sorvete de Morango",
            "quantidade": 7,
            "minimo": 10
        },
        {
            "nome": "Sorvete de Baunilha",
            "quantidade": 15,
            "minimo": 8
        }
    ]

    return render_template(
        "estoque.html",
        titulo="Controle de Estoque",
        estoque=estoque
    )
