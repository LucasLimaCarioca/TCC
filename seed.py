from app.app import create_app
from app.database import db

from app.models.produto import Produto
from app.models.estoque import Estoque

app = create_app()

with app.app_context():

    produto1 = Produto(
        nome="Sorvete de Chocolate",
        preco=15.0
    )

    produto2 = Produto(
        nome="Sorvete de Morango",
        preco=16.0
    )

    db.session.add_all([produto1, produto2])
    db.session.commit()

    estoque1 = Estoque(
        produto_id=produto1.id,
        quantidade_disponivel=50,
        estoque_minimo=10
    )

    estoque2 = Estoque(
        produto_id=produto2.id,
        quantidade_disponivel=8,
        estoque_minimo=10
    )

    db.session.add_all([estoque1, estoque2])
    db.session.commit()

    print("Dados inseridos!")