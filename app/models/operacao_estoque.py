"""Recibo persistido para repetição segura de operações de estoque/venda."""

from app.database import db
from app.models.timestamps import utc_now


class OperacaoEstoque(db.Model):
    __tablename__ = "operacoes_estoque"
    __table_args__ = (
        db.CheckConstraint("length(request_hash) = 64", name="ck_operacao_request_hash"),
        db.CheckConstraint("tipo IN ('venda', 'movimentacao')", name="ck_operacao_tipo"),
    )

    id = db.Column(db.String(36), primary_key=True)
    tipo = db.Column(db.String(30), nullable=False)
    request_hash = db.Column(db.String(64), nullable=False)
    resultado = db.Column(db.JSON, nullable=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now)
