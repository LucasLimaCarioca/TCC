from flask import (
    Blueprint,
    render_template,
    request
)

from app.agents.atendimento_agent import (
    AtendimentoAgent
)
from app.database import db
from app.models.historico_conversa import HistoricoConversa

venda_bp = Blueprint(
    "venda",
    __name__
)

# Instancia unica do agente simplificado que gera as respostas do atendimento.
agent = AtendimentoAgent()


def carregar_historico():
    # Reconstrói as mensagens da tela a partir do banco.
    # Isso mostra a persistencia conversacional da fase 2.
    registros = HistoricoConversa.query.order_by(
        HistoricoConversa.timestamp
    ).all()

    mensagens = []

    for registro in registros:
        mensagens.append({
            "tipo": "user",
            "texto": registro.mensagem_usuario
        })
        mensagens.append({
            "tipo": "bot",
            "texto": registro.resposta_agente
        })

    return mensagens


@venda_bp.route(
    "/",
    methods=["GET", "POST"]
)
def simulacao_venda():

    if request.method == "POST":

        # Pega a mensagem enviada pelo formulario do template.
        mensagem = request.form["mensagem"]

        # Envia a mensagem ao agente, que decide qual resposta devolver.
        resposta = agent.responder(mensagem)

        registro = HistoricoConversa(
            cliente_nome="Cliente Simulado",
            mensagem_usuario=mensagem,
            resposta_agente=resposta
        )

        db.session.add(registro)
        db.session.commit()

    # Renderiza a interface WhatsApp-like com o historico salvo no banco.
    return render_template(
        "simulacao_venda.html",
        titulo="Atendimento",
        mensagens=carregar_historico()
    )
