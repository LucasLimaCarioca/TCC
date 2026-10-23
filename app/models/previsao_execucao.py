"""Publicação atômica de uma execução de previsão, sem alterar o histórico."""

from app.database import db


class PrevisaoExecucao(db.Model):
    __tablename__ = "previsoes_execucoes"
    __table_args__ = (
        db.CheckConstraint("length(id) BETWEEN 1 AND 64", name="ck_previsao_execucao_id"),
        db.CheckConstraint(
            "length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_previsao_execucao_hash",
        ),
        db.CheckConstraint("horizonte_dias BETWEEN 1 AND 366", name="ck_previsao_execucao_horizonte"),
        db.CheckConstraint("ativa IN (0, 1)", name="ck_previsao_execucao_ativa"),
        db.Index("uq_previsao_execucao_ativa", "ativa", unique=True,
                 sqlite_where=db.text("ativa = 1")),
    )

    id = db.Column(db.String(64), primary_key=True)
    manifest_path = db.Column(db.String(255), nullable=False)
    manifest_sha256 = db.Column(db.String(64), nullable=False)
    gerada_em = db.Column(db.DateTime, nullable=False)
    data_corte = db.Column(db.Date, nullable=False)
    horizonte_dias = db.Column(db.Integer, nullable=False)
    versao_motor = db.Column(db.String(64), nullable=False)
    metricas_json = db.Column(db.JSON, nullable=False)
    # O snapshot conserva os decimais textuais e as somas gerais exatamente.
    resultado_json = db.Column(db.JSON, nullable=False)
    metadados_json = db.Column(db.JSON, nullable=False)
    ativa = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
