from flask import Blueprint, render_template, request

venda_bp = Blueprint("venda", __name__)

historico = []

@venda_bp.route("/", methods=["GET", "POST"])
def simulacao_venda():

    global historico

    if request.method == "POST":

        mensagem = request.form["mensagem"]

        historico.append({
            "tipo": "user",
            "texto": mensagem
        })

        resposta = gerar_resposta(mensagem)

        historico.append({
            "tipo": "bot",
            "texto": resposta
        })

    return render_template(
        "simulacao_venda.html",
        titulo="Simulação de Venda",
        mensagens=historico
    )

def gerar_resposta(mensagem):

    mensagem = mensagem.lower()

    if "pizza" in mensagem:
        return "Produto disponível no estoque."

    elif "hamburguer" in mensagem:
        return "Estoque baixo para este item."

    elif "oi" in mensagem:
        return "Olá! Como posso ajudar?"

    return "Solicitação recebida pelo agente."