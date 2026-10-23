"""Snapshot e integridade de alertas preventivos, preservando o histórico."""

from alembic import op
import sqlalchemy as sa

revision = "0008_alertas_preventivos"
down_revision = "0007_previsoes_execucoes"
branch_labels = None
depends_on = None


def upgrade():
    # Dados legados incompatíveis fazem a cópia ou o índice falhar. O comando
    # local de upgrade reverte a transação inteira e conserva o backup privado.
    with op.batch_alter_table("alertas_estoque") as batch:
        batch.add_column(sa.Column("previsao_json", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("atualizado_em", sa.DateTime(), nullable=True))
        batch.create_check_constraint(
            "ck_alerta_risco_produto",
            "tipo_alerta != 'risco_futuro' OR tipo_item = 'produto'",
        )
        batch.create_index(
            "uq_alerta_risco_futuro_ativo", ["produto_id"], unique=True,
            sqlite_where=sa.text("tipo_alerta = 'risco_futuro' AND ativo = 1"),
        )


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
