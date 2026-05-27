from app.database import db


class Estoque(db.Model):
    # Modelo preparado para a fase de banco de dados.
    # Na etapa 1, a tela de estoque ainda usa dados fixos na rota.
    __tablename__ = "estoque"

    id = db.Column(db.Integer, primary_key=True)

    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produtos.id")
    )

    quantidade_disponivel = db.Column(db.Integer)
    estoque_minimo = db.Column(db.Integer)
