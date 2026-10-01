from app.database import db
from app.models.timestamps import utc_now


class AlertaEstoque(db.Model):
    __tablename__ = "alertas_estoque"
    __table_args__ = (
        db.CheckConstraint(
            "(tipo_item = 'produto' AND produto_id IS NOT NULL AND materia_prima_id IS NULL) OR "
            "(tipo_item = 'materia_prima' AND materia_prima_id IS NOT NULL AND produto_id IS NULL)",
            name="ck_alerta_item",
        ),
        db.CheckConstraint("tipo_alerta IN ('estoque_minimo', 'risco_futuro')", name="ck_alerta_tipo"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tipo_item = db.Column(db.String(20), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey("produtos.id"), index=True)
    materia_prima_id = db.Column(db.Integer, db.ForeignKey("materias_primas.id"), index=True)
    tipo_alerta = db.Column(db.String(30), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    nivel = db.Column(db.String(20), nullable=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now)
    resolvido_em = db.Column(db.DateTime)
    ativo = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    produto = db.relationship("Produto")
    materia_prima = db.relationship("MateriaPrima")
