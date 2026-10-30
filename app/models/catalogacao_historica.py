"""Revisões explícitas e trilha de operações de associação, sem alterar vendas."""

from app.database import db
from app.models.timestamps import utc_now


class RevisaoAssociacaoHistorica(db.Model):
    __tablename__ = "revisoes_associacoes_historicas"
    __table_args__ = (
        db.CheckConstraint("length(codigo_produto) BETWEEN 1 AND 64", name="ck_revisao_codigo"),
        db.CheckConstraint("length(assinatura_produto) = 64 AND length(assinatura_historico) = 64",
                           name="ck_revisao_assinaturas"),
    )

    produto_id = db.Column(db.Integer, db.ForeignKey("produtos.id"), primary_key=True)
    codigo_produto = db.Column(db.String(64), nullable=False, index=True)
    assinatura_produto = db.Column(db.String(64), nullable=False)
    assinatura_historico = db.Column(db.String(64), nullable=False)
    operador = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    revisado_em = db.Column(db.DateTime, nullable=False, default=utc_now)


class OperacaoCatalogacao(db.Model):
    __tablename__ = "operacoes_catalogacao"
    __table_args__ = (
        db.CheckConstraint("length(request_hash) = 64", name="ck_catalogacao_request_hash"),
        db.CheckConstraint("acao IN ('associar','criar','revisar','alterar','desassociar','reverter')",
                           name="ck_catalogacao_acao"),
    )

    id = db.Column(db.String(36), primary_key=True)
    request_hash = db.Column(db.String(64), nullable=False)
    acao = db.Column(db.String(20), nullable=False)
    operador = db.Column(db.String(100), nullable=False)
    motivo = db.Column(db.String(500), nullable=False)
    antes_json = db.Column(db.JSON, nullable=False)
    depois_json = db.Column(db.JSON, nullable=False)
    resultado_json = db.Column(db.JSON, nullable=False)
    reverte_id = db.Column(db.String(36), db.ForeignKey("operacoes_catalogacao.id"), unique=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=utc_now)
