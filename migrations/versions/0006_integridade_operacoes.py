"""Restrições de integridade dos recibos, preservando as operações existentes."""

from alembic import op

revision = "0006_integridade_operacoes"
down_revision = "0005_operacoes_estoque"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("operacoes_estoque") as batch:
        batch.create_check_constraint("ck_operacao_request_hash", "length(request_hash) = 64")
        batch.create_check_constraint("ck_operacao_tipo", "tipo IN ('venda', 'movimentacao')")


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
