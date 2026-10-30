"""Catalogação auditável e origem de demonstração, sem inferir vínculos legados."""

from alembic import op
import sqlalchemy as sa

revision = "0009_catalogacao_historica"
down_revision = "0008_alertas_preventivos"
branch_labels = None
depends_on = None


def upgrade():
    # Legados conservam seus campos e vínculos. Ausência de revisão exige ação humana.
    op.add_column("produtos", sa.Column("demonstracao", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    op.create_table("revisoes_associacoes_historicas",
        sa.Column("produto_id", sa.Integer(), sa.ForeignKey("produtos.id"), primary_key=True),
        sa.Column("codigo_produto", sa.String(64), nullable=False),
        sa.Column("assinatura_produto", sa.String(64), nullable=False),
        sa.Column("assinatura_historico", sa.String(64), nullable=False),
        sa.Column("operador", sa.String(100), nullable=False),
        sa.Column("motivo", sa.String(500), nullable=False),
        sa.Column("revisado_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(codigo_produto) BETWEEN 1 AND 64", name="ck_revisao_codigo"),
        sa.CheckConstraint("length(assinatura_produto) = 64 AND length(assinatura_historico) = 64",
                           name="ck_revisao_assinaturas"))
    op.create_index("ix_revisoes_associacoes_historicas_codigo_produto",
                    "revisoes_associacoes_historicas", ["codigo_produto"])
    op.create_table("operacoes_catalogacao",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("acao", sa.String(20), nullable=False),
        sa.Column("operador", sa.String(100), nullable=False),
        sa.Column("motivo", sa.String(500), nullable=False),
        sa.Column("antes_json", sa.JSON(), nullable=False),
        sa.Column("depois_json", sa.JSON(), nullable=False),
        sa.Column("resultado_json", sa.JSON(), nullable=False),
        sa.Column("reverte_id", sa.String(36), sa.ForeignKey("operacoes_catalogacao.id"), unique=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(request_hash) = 64", name="ck_catalogacao_request_hash"),
        sa.CheckConstraint("acao IN ('associar','criar','revisar','alterar','desassociar','reverter')",
                           name="ck_catalogacao_acao"))


def downgrade():
    raise RuntimeError("Downgrade automático não disponível; restaure o backup local.")
