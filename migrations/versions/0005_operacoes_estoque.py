"""Identidade de operações e vínculo auditável entre venda e movimentação."""

from alembic import op
import sqlalchemy as sa

revision = "0005_operacoes_estoque"
down_revision = "0004_produto_saldo"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contextos_conversa", sa.Column("operacao_id", sa.String(36)))
    # Coluna nullable preserva movimentações legadas sem inventar vínculos.
    with op.batch_alter_table("movimentacoes_estoque") as batch:
        batch.add_column(sa.Column("venda_id", sa.Integer))
        batch.create_foreign_key("fk_movimentacao_venda", "vendas", ["venda_id"], ["id"])
        batch.create_unique_constraint("uq_movimentacao_venda", ["venda_id"])
    op.create_table(
        "operacoes_estoque",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("resultado", sa.JSON, nullable=False),
        sa.Column("criado_em", sa.DateTime, nullable=False),
    )


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
