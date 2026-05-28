from datetime import datetime

from app.database import db


class HistoricoConversa(db.Model):

    __tablename__ = "historico_conversas"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    cliente_nome = db.Column(
        db.String(100),
        nullable=False,
        default="Cliente Simulado"
    )

    mensagem_usuario = db.Column(
        db.Text,
        nullable=False
    )

    resposta_agente = db.Column(
        db.Text,
        nullable=False
    )

    timestamp = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )
