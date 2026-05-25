from app.database import db

class Estoque(db.Model):
    __tablename__ = "estoque"

    id = db.Column(db.Integer, primary_key=True)

    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produtos.id")
    )

    quantidade_disponivel = db.Column(db.Integer)
    estoque_minimo = db.Column(db.Integer)