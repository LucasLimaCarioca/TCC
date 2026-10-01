"""Impede saldo negativo em produtos, mantendo estoque zerado válido."""

from alembic import op
import sqlalchemy as sa

revision = "0004_produto_saldo"
down_revision = "0003_movimentacao_quantidade"
branch_labels = None
depends_on = None


def upgrade():
    invalid = op.get_bind().execute(sa.text(
        "SELECT 1 FROM produtos WHERE quantidade_disponivel < 0 LIMIT 1"
    )).first()
    if invalid is not None:
        raise RuntimeError("Existem produtos com saldo negativo; revisão necessária.")
    # O runner suspende FKs só nesta conexão, fora da transação, para permitir
    # reconstruir a tabela pai. Ele verifica todas as referências antes do commit.
    with op.batch_alter_table("produtos") as batch:
        batch.create_check_constraint("ck_produto_saldo", "quantidade_disponivel >= 0")


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
