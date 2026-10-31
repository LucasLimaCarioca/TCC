"""Qualifica explicitamente vínculos experimentais sem reclassificar legados."""

from alembic import op
import sqlalchemy as sa

revision = "0010_associacoes_experimentais"
down_revision = "0009_catalogacao_historica"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("revisoes_associacoes_historicas") as batch:
        batch.add_column(sa.Column("tipo_vinculo", sa.String(20), nullable=False, server_default="operacional"))
        batch.add_column(sa.Column("selecionado_previsao", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("compatibilidade_json", sa.JSON(none_as_null=True), nullable=True))
        batch.create_check_constraint("ck_revisao_tipo_vinculo", "tipo_vinculo IN ('operacional', 'experimental')")
        batch.create_check_constraint("ck_revisao_selecao_experimental",
            "selecionado_previsao IN (0, 1) AND (selecionado_previsao = 0 OR tipo_vinculo = 'experimental')")


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
