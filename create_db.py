from app.app import create_app
from app.database import db

from app.models.produto import Produto
from app.models.venda import Venda

app = create_app()

with app.app_context():

    db.create_all()

    print("Banco criado com sucesso!")