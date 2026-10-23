"""Publicações de previsão e códigos históricos, preservando previsões legadas."""

from alembic import op
import sqlalchemy as sa

revision = "0007_previsoes_execucoes"
down_revision = "0006_integridade_operacoes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "previsoes_execucoes",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("manifest_path", sa.String(255), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("gerada_em", sa.DateTime(), nullable=False),
        sa.Column("data_corte", sa.Date(), nullable=False),
        sa.Column("horizonte_dias", sa.Integer(), nullable=False),
        sa.Column("versao_motor", sa.String(64), nullable=False),
        sa.Column("metricas_json", sa.JSON(), nullable=False),
        sa.Column("resultado_json", sa.JSON(), nullable=False),
        sa.Column("metadados_json", sa.JSON(), nullable=False),
        sa.Column("ativa", sa.Boolean(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("length(id) BETWEEN 1 AND 64", name="ck_previsao_execucao_id"),
        sa.CheckConstraint("length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'",
                           name="ck_previsao_execucao_hash"),
        sa.CheckConstraint("horizonte_dias BETWEEN 1 AND 366", name="ck_previsao_execucao_horizonte"),
        sa.CheckConstraint("ativa IN (0, 1)", name="ck_previsao_execucao_ativa"),
    )
    op.create_index("uq_previsao_execucao_ativa", "previsoes_execucoes", ["ativa"],
                    unique=True, sqlite_where=sa.text("ativa = 1"))
    with op.batch_alter_table("previsoes_demanda") as batch:
        batch.alter_column("produto_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("execucao_id", sa.String(64)))
        batch.add_column(sa.Column("codigo_produto", sa.String(64)))
        batch.add_column(sa.Column("quantidade_exata", sa.String(26)))
        batch.add_column(sa.Column("cobertura_inicio", sa.Date()))
        batch.add_column(sa.Column("cobertura_fim", sa.Date()))
        batch.create_foreign_key("fk_previsao_execucao", "previsoes_execucoes", ["execucao_id"], ["id"])
        batch.create_index("ix_previsoes_demanda_execucao_id", ["execucao_id"])
        batch.create_unique_constraint("uq_previsao_execucao_periodo",
                                       ["execucao_id", "codigo_produto", "granularidade", "periodo_inicio"])
        batch.create_check_constraint(
            "ck_previsao_origem",
            "(execucao_id IS NULL AND produto_id IS NOT NULL AND codigo_produto IS NULL "
            "AND quantidade_exata IS NULL AND cobertura_inicio IS NULL AND cobertura_fim IS NULL) OR "
            "(execucao_id IS NOT NULL AND codigo_produto IS NOT NULL AND quantidade_exata IS NOT NULL "
            "AND cobertura_inicio IS NOT NULL AND cobertura_fim IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_previsao_codigo", "execucao_id IS NULL OR (length(codigo_produto) BETWEEN 1 AND 64 "
            "AND codigo_produto = trim(codigo_produto))",
        )
        batch.create_check_constraint(
            "ck_previsao_cobertura", "execucao_id IS NULL OR (periodo_inicio <= cobertura_inicio "
            "AND cobertura_inicio <= cobertura_fim AND cobertura_fim <= periodo_fim)",
        )
        batch.create_check_constraint(
            "ck_previsao_quantidade_exata", "execucao_id IS NULL OR (length(quantidade_exata) BETWEEN 1 AND 26 "
            "AND quantidade_exata NOT GLOB '*[^0-9.]*' "
            "AND substr(quantidade_exata, 1, 1) != '.' AND substr(quantidade_exata, -1) != '.' "
            "AND length(quantidade_exata) - length(replace(quantidade_exata, '.', '')) <= 1 "
            "AND ((instr(quantidade_exata, '.') = 0 AND length(quantidade_exata) <= 16) "
            "OR (instr(quantidade_exata, '.') BETWEEN 2 AND 17 "
            "AND length(quantidade_exata) - instr(quantidade_exata, '.') BETWEEN 1 AND 9)))",
        )
        batch.create_check_constraint("ck_previsao_metricas_execucao", "execucao_id IS NULL OR metricas_json IS NULL")


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
