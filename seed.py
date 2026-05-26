from app.app import create_app
from app.database import db

from app.models.produto import Produto

app = create_app()

with app.app_context():

    produtos = [

        Produto(
            nome="Sorvete de Chocolate",
            preco=15.0
        ),

        Produto(
            nome="Sorvete de Morango",
            preco=16.0
        ),

        Produto(
            nome="Sorvete de Baunilha",
            preco=14.0
        )
    ]

    db.session.add_all(produtos)

    db.session.commit()

    print("Produtos cadastrados!")