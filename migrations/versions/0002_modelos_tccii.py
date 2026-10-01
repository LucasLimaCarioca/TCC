"""Adiciona os modelos do TCC II sem remover dados do baseline."""

from alembic import op
import sqlalchemy as sa

revision = "0002_modelos_tccii"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('logs_mensagens_agentes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sender', sa.String(length=255), nullable=False),
    sa.Column('receiver', sa.String(length=255), nullable=False),
    sa.Column('performative', sa.String(length=30), nullable=False),
    sa.Column('ontology', sa.String(length=100), nullable=False),
    sa.Column('thread', sa.String(length=100), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_logs_mensagens_agentes_thread'), 'logs_mensagens_agentes', ['thread'], unique=False)
    op.create_table('materias_primas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('codigo', sa.String(length=64), nullable=False),
    sa.Column('nome', sa.String(length=100), nullable=False),
    sa.Column('unidade_medida', sa.String(length=20), nullable=False),
    sa.Column('quantidade_disponivel', sa.Numeric(precision=25, scale=9), server_default='0', nullable=False),
    sa.Column('estoque_minimo', sa.Numeric(precision=25, scale=9), server_default='0', nullable=False),
    sa.Column('ativo', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint('estoque_minimo >= 0', name='ck_materia_prima_minimo'),
    sa.CheckConstraint('quantidade_disponivel >= 0', name='ck_materia_prima_saldo'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('codigo')
    )
    op.create_table('vendas_historicas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('codigo_produto', sa.String(length=64), nullable=False),
    sa.Column('data', sa.Date(), nullable=False),
    sa.Column('codigo_grupo', sa.String(length=64), nullable=True),
    sa.Column('categoria', sa.String(length=100), nullable=True),
    sa.Column('quantidade', sa.Numeric(precision=25, scale=9), nullable=False),
    sa.Column('numero_pedidos', sa.Integer(), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('dataset_hash', sa.String(length=64), nullable=False),
    sa.Column('importado_em', sa.DateTime(), nullable=False),
    sa.CheckConstraint('length(source_hash) = 64 AND length(dataset_hash) = 64', name='ck_historico_hashes'),
    sa.CheckConstraint('numero_pedidos >= 0', name='ck_historico_pedidos'),
    sa.CheckConstraint('quantidade >= 0', name='ck_historico_quantidade'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('codigo_produto', 'data', name='uq_historico_produto_data'),
    sa.UniqueConstraint('source_hash')
    )
    op.create_index(op.f('ix_vendas_historicas_data'), 'vendas_historicas', ['data'], unique=False)
    op.create_table('alertas_estoque',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tipo_item', sa.String(length=20), nullable=False),
    sa.Column('produto_id', sa.Integer(), nullable=True),
    sa.Column('materia_prima_id', sa.Integer(), nullable=True),
    sa.Column('tipo_alerta', sa.String(length=30), nullable=False),
    sa.Column('mensagem', sa.Text(), nullable=False),
    sa.Column('nivel', sa.String(length=20), nullable=False),
    sa.Column('criado_em', sa.DateTime(), nullable=False),
    sa.Column('resolvido_em', sa.DateTime(), nullable=True),
    sa.Column('ativo', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.CheckConstraint("(tipo_item = 'produto' AND produto_id IS NOT NULL AND materia_prima_id IS NULL) OR (tipo_item = 'materia_prima' AND materia_prima_id IS NOT NULL AND produto_id IS NULL)", name='ck_alerta_item'),
    sa.CheckConstraint("tipo_alerta IN ('estoque_minimo', 'risco_futuro')", name='ck_alerta_tipo'),
    sa.ForeignKeyConstraint(['materia_prima_id'], ['materias_primas.id'], ),
    sa.ForeignKeyConstraint(['produto_id'], ['produtos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alertas_estoque_materia_prima_id'), 'alertas_estoque', ['materia_prima_id'], unique=False)
    op.create_index(op.f('ix_alertas_estoque_produto_id'), 'alertas_estoque', ['produto_id'], unique=False)
    op.create_table('movimentacoes_estoque',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tipo_item', sa.String(length=20), nullable=False),
    sa.Column('produto_id', sa.Integer(), nullable=True),
    sa.Column('materia_prima_id', sa.Integer(), nullable=True),
    sa.Column('tipo_movimentacao', sa.String(length=20), nullable=False),
    sa.Column('quantidade', sa.Numeric(precision=25, scale=9), nullable=False),
    sa.Column('saldo_anterior', sa.Numeric(precision=25, scale=9), nullable=False),
    sa.Column('saldo_posterior', sa.Numeric(precision=25, scale=9), nullable=False),
    sa.Column('motivo', sa.Text(), nullable=False),
    sa.Column('criado_em', sa.DateTime(), nullable=False),
    sa.CheckConstraint("(tipo_item = 'produto' AND produto_id IS NOT NULL AND materia_prima_id IS NULL) OR (tipo_item = 'materia_prima' AND materia_prima_id IS NOT NULL AND produto_id IS NULL)", name='ck_movimentacao_item'),
    sa.CheckConstraint("tipo_movimentacao IN ('entrada', 'saida', 'ajuste', 'venda')", name='ck_movimentacao_tipo'),
    sa.CheckConstraint('saldo_anterior >= 0 AND saldo_posterior >= 0', name='ck_movimentacao_saldos'),
    sa.ForeignKeyConstraint(['materia_prima_id'], ['materias_primas.id'], ),
    sa.ForeignKeyConstraint(['produto_id'], ['produtos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_movimentacoes_estoque_materia_prima_id'), 'movimentacoes_estoque', ['materia_prima_id'], unique=False)
    op.create_index(op.f('ix_movimentacoes_estoque_produto_id'), 'movimentacoes_estoque', ['produto_id'], unique=False)
    op.create_table('previsoes_demanda',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('produto_id', sa.Integer(), nullable=False),
    sa.Column('granularidade', sa.String(length=20), nullable=False),
    sa.Column('periodo_inicio', sa.Date(), nullable=False),
    sa.Column('periodo_fim', sa.Date(), nullable=False),
    sa.Column('quantidade_prevista', sa.Numeric(precision=25, scale=9), nullable=False),
    sa.Column('modelo', sa.String(length=100), nullable=False),
    sa.Column('versao_modelo', sa.String(length=64), nullable=False),
    sa.Column('gerada_em', sa.DateTime(), nullable=False),
    sa.Column('metricas_json', sa.JSON(), nullable=True),
    sa.CheckConstraint("granularidade IN ('diaria', 'semanal', 'mensal')", name='ck_previsao_granularidade'),
    sa.CheckConstraint('periodo_fim >= periodo_inicio', name='ck_previsao_periodo'),
    sa.CheckConstraint('quantidade_prevista >= 0', name='ck_previsao_quantidade'),
    sa.ForeignKeyConstraint(['produto_id'], ['produtos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_previsoes_demanda_produto_id'), 'previsoes_demanda', ['produto_id'], unique=False)
    op.add_column('produtos', sa.Column('codigo_externo', sa.String(length=64), nullable=True))
    op.add_column('produtos', sa.Column('estoque_minimo', sa.Integer(), sa.CheckConstraint('estoque_minimo >= 0', name='ck_produto_minimo'), server_default='0', nullable=False))
    op.create_index(op.f('ix_produtos_codigo_externo'), 'produtos', ['codigo_externo'], unique=True)


def downgrade():
    raise RuntimeError("Downgrade destrutivo não disponível; restaure o backup local.")
