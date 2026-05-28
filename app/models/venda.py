from app.database import db
from datetime import datetime


class Venda(db.Model):

    __tablename__ = "vendas"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    cliente_nome = db.Column(
        db.String(100),
        nullable=False,
        default="Cliente Simulado"
    )

    # Guarda a referencia ao produto vendido.
    # Assim a venda continua ligada ao cadastro de produtos.
    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produtos.id"),
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

    produto = db.relationship(
        "Produto",
        back_populates="vendas"
    )
