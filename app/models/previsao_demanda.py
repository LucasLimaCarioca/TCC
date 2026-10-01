from app.database import db
from app.models.timestamps import utc_now


class PrevisaoDemanda(db.Model):
    __tablename__ = "previsoes_demanda"
    __table_args__ = (
        db.CheckConstraint("granularidade IN ('diaria', 'semanal', 'mensal')", name="ck_previsao_granularidade"),
        db.CheckConstraint("periodo_fim >= periodo_inicio", name="ck_previsao_periodo"),
        db.CheckConstraint("quantidade_prevista >= 0", name="ck_previsao_quantidade"),
    )

    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(db.Integer, db.ForeignKey("produtos.id"), nullable=False, index=True)
    granularidade = db.Column(db.String(20), nullable=False)
    periodo_inicio = db.Column(db.Date, nullable=False)
    periodo_fim = db.Column(db.Date, nullable=False)
    quantidade_prevista = db.Column(db.Numeric(25, 9), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    versao_modelo = db.Column(db.String(64), nullable=False)
    gerada_em = db.Column(db.DateTime, nullable=False, default=utc_now)
    metricas_json = db.Column(db.JSON)

    produto = db.relationship("Produto")
