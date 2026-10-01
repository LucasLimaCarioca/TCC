"""Adota bancos do TCC I e cria o baseline em bancos vazios."""

from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "produtos" not in tables:
        op.create_table(
            "produtos",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("nome", sa.String(100), nullable=False, unique=True),
            sa.Column("categoria", sa.String(100), nullable=False),
            sa.Column("sabor", sa.String(100), nullable=False),
            sa.Column("preco", sa.Float, nullable=False),
            sa.Column("descricao", sa.String(255)),
            sa.Column("quantidade_disponivel", sa.Integer, nullable=False),
            sa.Column("ativo", sa.Boolean, nullable=False),
        )
    if "clientes" not in tables:
        op.create_table(
            "clientes", sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("nome", sa.String(100), nullable=False),
            sa.Column("telefone", sa.String(20)),
        )
    if "historico_conversas" not in tables:
        op.create_table(
            "historico_conversas", sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("cliente_nome", sa.String(100), nullable=False),
            sa.Column("mensagem_usuario", sa.Text, nullable=False),
            sa.Column("resposta_agente", sa.Text, nullable=False),
            sa.Column("timestamp", sa.DateTime),
        )
    if "contextos_conversa" not in tables:
        op.create_table(
            "contextos_conversa", sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("cliente_nome", sa.String(100), nullable=False, unique=True),
            sa.Column("etapa", sa.String(50), nullable=False),
            sa.Column("produto_id", sa.Integer, sa.ForeignKey("produtos.id")),
            sa.Column("quantidade", sa.Integer), sa.Column("itens_json", sa.Text),
            sa.Column("atualizado_em", sa.DateTime),
        )
    if "vendas" not in tables:
        op.create_table(
            "vendas", sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("cliente_nome", sa.String(100), nullable=False),
            sa.Column("produto_id", sa.Integer, sa.ForeignKey("produtos.id"), nullable=False),
            sa.Column("quantidade", sa.Integer, nullable=False),
            sa.Column("valor_total", sa.Float, nullable=False),
            sa.Column("data_venda", sa.DateTime),
        )
    # Evoluções legadas que antes ficavam em create_db.py.
    additions = {
        "produtos": [
            sa.Column("descricao", sa.String(255)),
            sa.Column("categoria", sa.String(100), nullable=False, server_default="sorvete simples"),
            sa.Column("sabor", sa.String(100), nullable=False, server_default="tradicional"),
            sa.Column("quantidade_disponivel", sa.Integer, nullable=False, server_default="0"),
            sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        ],
        "vendas": [sa.Column("cliente_nome", sa.String(100), nullable=False, server_default="Cliente Simulado")],
        "contextos_conversa": [sa.Column("itens_json", sa.Text)],
    }
    for table, columns in additions.items():
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    # Preserva a tabela antiga como evidência, após transferir seu saldo.
    if "estoque" in tables:
        op.execute(sa.text(
            "UPDATE produtos SET quantidade_disponivel = "
            "(SELECT quantidade_disponivel FROM estoque WHERE produto_id = produtos.id) "
            "WHERE id IN (SELECT produto_id FROM estoque)"
        ))


def downgrade():
    raise RuntimeError("Downgrade destrutivo não disponível; restaure o backup local.")
