from app.database import db


class MateriaPrima(db.Model):
    __tablename__ = "materias_primas"
    __table_args__ = (
        db.CheckConstraint("quantidade_disponivel >= 0", name="ck_materia_prima_saldo"),
        db.CheckConstraint("estoque_minimo >= 0", name="ck_materia_prima_minimo"),
    )

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(64), nullable=False, unique=True)
    nome = db.Column(db.String(100), nullable=False)
    unidade_medida = db.Column(db.String(20), nullable=False)
    quantidade_disponivel = db.Column(db.Numeric(25, 9), nullable=False, default=0, server_default="0")
    estoque_minimo = db.Column(db.Numeric(25, 9), nullable=False, default=0, server_default="0")
    ativo = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
