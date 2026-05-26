from app.database import db
from datetime import datetime

class Venda(db.Model):

    __tablename__ = "vendas"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    produto = db.Column(
        db.String(100),
        nullable=False
    )

    quantidade = db.Column(
        db.Integer,
        nullable=False
    )

    valor_total = db.Column(
        db.Float,
        nullable=False
    )

    data_venda = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )