from app.database import db
from app.models.timestamps import utc_now


class VendaHistorica(db.Model):
    """Uma linha diária do dataset aprovado, sem clientes/vendedores/pedidos brutos."""

    __tablename__ = "vendas_historicas"
    __table_args__ = (
        db.UniqueConstraint("codigo_produto", "data", name="uq_historico_produto_data"),
        db.CheckConstraint("quantidade >= 0", name="ck_historico_quantidade"),
        db.CheckConstraint("numero_pedidos >= 0", name="ck_historico_pedidos"),
        db.CheckConstraint("length(source_hash) = 64 AND length(dataset_hash) = 64", name="ck_historico_hashes"),
    )

    id = db.Column(db.Integer, primary_key=True)
    codigo_produto = db.Column(db.String(64), nullable=False)
    data = db.Column(db.Date, nullable=False, index=True)
    codigo_grupo = db.Column(db.String(64))
    categoria = db.Column(db.String(100))
    quantidade = db.Column(db.Numeric(25, 9), nullable=False)
    numero_pedidos = db.Column(db.Integer, nullable=False)
    source_hash = db.Column(db.String(64), nullable=False, unique=True)
    dataset_hash = db.Column(db.String(64), nullable=False)
    importado_em = db.Column(db.DateTime, nullable=False, default=utc_now)
