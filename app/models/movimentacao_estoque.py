from app.database import db
from app.models.timestamps import utc_now


class MovimentacaoEstoque(db.Model):
    __tablename__ = "movimentacoes_estoque"
    __table_args__ = (
        db.CheckConstraint(
            "(tipo_item = 'produto' AND produto_id IS NOT NULL AND materia_prima_id IS NULL) OR "
            "(tipo_item = 'materia_prima' AND materia_prima_id IS NOT NULL AND produto_id IS NULL)",
            name="ck_movimentacao_item",
        ),
        db.CheckConstraint("tipo_movimentacao IN ('entrada', 'saida', 'ajuste', 'venda')", name="ck_movimentacao_tipo"),
        db.CheckConstraint("quantidade > 0", name="ck_movimentacao_quantidade_positiva"),
        db.CheckConstraint("saldo_anterior >= 0 AND saldo_posterior >= 0", name="ck_movimentacao_saldos"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tipo_item = db.Column(db.String(20), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey("produtos.id"), index=True)
    materia_prima_id = db.Column(db.Integer, db.ForeignKey("materias_primas.id"), index=True)
    tipo_movimentacao = db.Column(db.String(20), nullable=False)
    quantidade = db.Column(db.Numeric(25, 9), nullable=False)
    saldo_anterior = db.Column(db.Numeric(25, 9), nullable=False)
    saldo_posterior = db.Column(db.Numeric(25, 9), nullable=False)
    motivo = db.Column(db.Text, nullable=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now)

    produto = db.relationship("Produto")
    materia_prima = db.relationship("MateriaPrima")
