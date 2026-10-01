"""Exige quantidade estritamente positiva nas movimentações de estoque."""

from alembic import op
import sqlalchemy as sa

revision = "0003_movimentacao_quantidade"
down_revision = "0002_modelos_tccii"
branch_labels = None
depends_on = None


def upgrade():
    # Não corrige nem elimina registros legados silenciosamente.
    invalid = op.get_bind().execute(sa.text(
        "SELECT 1 FROM movimentacoes_estoque WHERE quantidade <= 0 LIMIT 1"
    )).first()
    if invalid is not None:
        raise RuntimeError("Existem movimentações com quantidade não positiva; revisão necessária.")
    # SQLite exige reconstruir a tabela para acrescentar um CHECK.
    # O batch preserva registros, referências, índices e restrições existentes.
    with op.batch_alter_table("movimentacoes_estoque") as batch:
        batch.create_check_constraint("ck_movimentacao_quantidade_positiva", "quantidade > 0")


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
