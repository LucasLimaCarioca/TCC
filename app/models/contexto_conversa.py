from datetime import datetime

from app.database import db


class ContextoConversa(db.Model):

    __tablename__ = "contextos_conversa"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # Cada cliente possui no maximo um contexto aberto.
    # Isso permite continuar um fluxo em mais de uma mensagem.
    cliente_nome = db.Column(
        db.String(100),
        nullable=False,
        unique=True
    )

    etapa = db.Column(
        db.String(50),
        nullable=False
    )

    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produtos.id"),
        nullable=True
    )

    quantidade = db.Column(
        db.Integer,
        nullable=True
    )

    # JSON textual com todos os itens do pedido pendente.
    # Exemplo: [{"produto_id": 1, "quantidade": 2}, {"produto_id": 2, "quantidade": 1}]
    itens_json = db.Column(
        db.Text,
        nullable=True
    )

    atualizado_em = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    produto = db.relationship("Produto")
