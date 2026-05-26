from flask import (
    Blueprint,
    render_template,
    request
)

from app.agents.atendimento_agent import (
    AtendimentoAgent
)

venda_bp = Blueprint(
    "venda",
    __name__
)

historico = []

agent = AtendimentoAgent()

@venda_bp.route(
    "/",
    methods=["GET", "POST"]
)
def simulacao_venda():

    global historico

    if request.method == "POST":

        mensagem = request.form["mensagem"]

        historico.append({
            "tipo": "user",
            "texto": mensagem
        })

        resposta = agent.responder(mensagem)

        historico.append({
            "tipo": "bot",
            "texto": resposta
        })

    return render_template(
        "simulacao_venda.html",
        titulo="Atendimento",
        mensagens=historico
    )