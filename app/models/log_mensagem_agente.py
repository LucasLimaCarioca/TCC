from app.database import db
from app.models.timestamps import utc_now


class LogMensagemAgente(db.Model):
    """Estrutura para mensagens futuras; o emissor deve remover dados pessoais."""

    __tablename__ = "logs_mensagens_agentes"

    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(255), nullable=False)
    receiver = db.Column(db.String(255), nullable=False)
    performative = db.Column(db.String(30), nullable=False)
    ontology = db.Column(db.String(100), nullable=False)
    thread = db.Column(db.String(100), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=utc_now)
    status = db.Column(db.String(30), nullable=False)
