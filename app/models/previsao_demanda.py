from app.database import db
from app.models.timestamps import utc_now


class PrevisaoDemanda(db.Model):
    __tablename__ = "previsoes_demanda"
    __table_args__ = (
        db.CheckConstraint("granularidade IN ('diaria', 'semanal', 'mensal')", name="ck_previsao_granularidade"),
        db.CheckConstraint("periodo_fim >= periodo_inicio", name="ck_previsao_periodo"),
        db.CheckConstraint("quantidade_prevista >= 0", name="ck_previsao_quantidade"),
        db.CheckConstraint(
            "(execucao_id IS NULL AND produto_id IS NOT NULL AND codigo_produto IS NULL "
            "AND quantidade_exata IS NULL AND cobertura_inicio IS NULL AND cobertura_fim IS NULL) OR "
            "(execucao_id IS NOT NULL AND codigo_produto IS NOT NULL AND quantidade_exata IS NOT NULL "
            "AND cobertura_inicio IS NOT NULL AND cobertura_fim IS NOT NULL)",
            name="ck_previsao_origem",
        ),
        db.CheckConstraint(
            "execucao_id IS NULL OR (length(codigo_produto) BETWEEN 1 AND 64 "
            "AND codigo_produto = trim(codigo_produto))", name="ck_previsao_codigo",
        ),
        db.CheckConstraint(
            "execucao_id IS NULL OR (periodo_inicio <= cobertura_inicio "
            "AND cobertura_inicio <= cobertura_fim AND cobertura_fim <= periodo_fim)",
            name="ck_previsao_cobertura",
        ),
        db.CheckConstraint(
            "execucao_id IS NULL OR (length(quantidade_exata) BETWEEN 1 AND 26 "
            "AND quantidade_exata NOT GLOB '*[^0-9.]*' "
            "AND substr(quantidade_exata, 1, 1) != '.' AND substr(quantidade_exata, -1) != '.' "
            "AND length(quantidade_exata) - length(replace(quantidade_exata, '.', '')) <= 1 "
            "AND ((instr(quantidade_exata, '.') = 0 AND length(quantidade_exata) <= 16) "
            "OR (instr(quantidade_exata, '.') BETWEEN 2 AND 17 "
            "AND length(quantidade_exata) - instr(quantidade_exata, '.') BETWEEN 1 AND 9)))",
            name="ck_previsao_quantidade_exata",
        ),
        db.CheckConstraint("execucao_id IS NULL OR metricas_json IS NULL", name="ck_previsao_metricas_execucao"),
        db.UniqueConstraint("execucao_id", "codigo_produto", "granularidade", "periodo_inicio",
                            name="uq_previsao_execucao_periodo"),
    )

    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(db.Integer, db.ForeignKey("produtos.id"), index=True)
    granularidade = db.Column(db.String(20), nullable=False)
    periodo_inicio = db.Column(db.Date, nullable=False)
    periodo_fim = db.Column(db.Date, nullable=False)
    quantidade_prevista = db.Column(db.Numeric(25, 9), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    versao_modelo = db.Column(db.String(64), nullable=False)
    gerada_em = db.Column(db.DateTime, nullable=False, default=utc_now)
    metricas_json = db.Column(db.JSON(none_as_null=True))
    execucao_id = db.Column(db.String(64), db.ForeignKey("previsoes_execucoes.id"), index=True)
    codigo_produto = db.Column(db.String(64))
    quantidade_exata = db.Column(db.String(26))
    cobertura_inicio = db.Column(db.Date)
    cobertura_fim = db.Column(db.Date)

    produto = db.relationship("Produto")
    execucao = db.relationship("PrevisaoExecucao")
