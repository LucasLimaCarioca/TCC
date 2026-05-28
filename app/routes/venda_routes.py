from flask import (
    Blueprint,
    jsonify,
    render_template,
    request
)

from app.agents.atendimento_agent import (
    AtendimentoAgent
)
from app.database import db
from app.models.historico_conversa import HistoricoConversa
from app.models.produto import Produto
from app.models.venda import Venda
from app.services.venda_service import registrar_venda

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


@venda_bp.route(
    "/vendas",
    methods=["GET", "POST"]
)
def tela_vendas():
    mensagem = None
    erro = None

    if request.method == "POST":
        produto_id = int(request.form["produto_id"])
        quantidade = int(request.form["quantidade"])
        cliente_nome = request.form.get("cliente_nome") or "Cliente Simulado"

        sucesso, mensagem_retorno, venda = registrar_venda(
            produto_id,
            quantidade,
            cliente_nome=cliente_nome
        )

        if sucesso:
            mensagem = mensagem_retorno
        else:
            erro = mensagem_retorno

    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(Produto.nome).all()

    vendas = Venda.query.order_by(
        Venda.data_venda.desc()
    ).limit(10).all()

    return render_template(
        "vendas.html",
        titulo="Vendas",
        produtos=produtos,
        vendas=vendas,
        mensagem=mensagem,
        erro=erro
    )


@venda_bp.route("/api/vendas", methods=["GET"])
def listar_vendas_api():
    vendas = Venda.query.order_by(
        Venda.data_venda.desc()
    ).all()

    return jsonify([
        {
            "id": venda.id,
            "cliente_nome": venda.cliente_nome,
            "produto": venda.produto.nome if venda.produto else None,
            "produto_id": venda.produto_id,
            "quantidade": venda.quantidade,
            "valor_total": venda.valor_total,
            "data_venda": venda.data_venda.isoformat() if venda.data_venda else None
        }
        for venda in vendas
    ])


@venda_bp.route("/api/vendas", methods=["POST"])
def cadastrar_venda_api():
    dados = request.get_json(silent=True) or request.form

    produto_id = int(dados["produto_id"])
    quantidade = int(dados["quantidade"])
    cliente_nome = dados.get("cliente_nome") or "Cliente Simulado"

    sucesso, mensagem, venda = registrar_venda(
        produto_id,
        quantidade,
        cliente_nome=cliente_nome
    )

    if not sucesso:
        return jsonify({"erro": mensagem}), 400

    return jsonify({
        "mensagem": mensagem,
        "venda": {
            "id": venda.id,
            "cliente_nome": venda.cliente_nome,
            "produto_id": venda.produto_id,
            "produto": venda.produto.nome,
            "quantidade": venda.quantidade,
            "valor_total": venda.valor_total
        }
    }), 201
