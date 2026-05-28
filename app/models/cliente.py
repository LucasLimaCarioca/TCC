from app.database import db


class Cliente(db.Model):

    __tablename__ = "clientes"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # Cliente simples para simular usuarios do atendimento.
    nome = db.Column(
        db.String(100),
        nullable=False
    )

    telefone = db.Column(
        db.String(20),
        nullable=True
    )
