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

# Historico em memoria para a etapa 1.
# Ele funciona enquanto o servidor esta rodando, mas sera perdido ao reiniciar.
historico = []

# Instancia unica do agente simplificado que gera as respostas do atendimento.
agent = AtendimentoAgent()

@venda_bp.route(
    "/",
    methods=["GET", "POST"]
)
def simulacao_venda():

    global historico

    if request.method == "POST":

        # Pega a mensagem enviada pelo formulario do template.
        mensagem = request.form["mensagem"]

        # Guarda a fala do usuario para exibir novamente na tela.
        historico.append({
            "tipo": "user",
            "texto": mensagem
        })

        # Envia a mensagem ao agente, que decide qual resposta devolver.
        resposta = agent.responder(mensagem)

        # Guarda a resposta do agente no mesmo historico da conversa.
        historico.append({
            "tipo": "bot",
            "texto": resposta
        })

    # Renderiza a interface WhatsApp-like com todo o historico atual.
    return render_template(
        "simulacao_venda.html",
        titulo="Atendimento",
        mensagens=historico
    )
