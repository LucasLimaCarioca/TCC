from app.database import db
from datetime import datetime

class Venda(db.Model):
    __tablename__ = "vendas"

    id = db.Column(db.Integer, primary_key=True)

    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produtos.id")
    )

    quantidade = db.Column(db.Integer)
    valor_total = db.Column(db.Float)

    data_venda = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )