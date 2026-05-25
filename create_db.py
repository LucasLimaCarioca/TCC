from app.app import create_app
from app.database import db

app = create_app()

with app.app_context():
    db.create_all()

    print("Banco criado com sucesso!")