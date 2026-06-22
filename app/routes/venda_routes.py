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
from app.models.cliente import Cliente
from app.models.historico_conversa import HistoricoConversa
from app.models.produto import Produto
from app.models.venda import Venda
from app.services.venda_service import registrar_venda

# Blueprint agrupa as rotas relacionadas ao atendimento e às vendas.
# Ele é registrado em app/app.py dentro da função create_app().
venda_bp = Blueprint(
    "venda",
    __name__
)

# Instancia unica do agente simplificado que gera as respostas do atendimento.
agent = AtendimentoAgent()


def buscar_cliente_selecionado():
    # O atendimento visual trabalha com tres clientes simulados.
    # O cliente selecionado vem pela URL (?cliente_id=), permitindo trocar conversas.
    # request.values lê tanto query string (GET) quanto formulário (POST).
    cliente_id = request.values.get("cliente_id", type=int)

    if cliente_id is not None:
        # db.session.get busca pela chave primaria do modelo.
        cliente = db.session.get(Cliente, cliente_id)

        if cliente is not None:
            return cliente

    # Se nenhum cliente válido for informado, usa o primeiro cliente cadastrado.
    return Cliente.query.order_by(Cliente.id).first()


def carregar_historico(cliente_nome):
    # Reconstrói as mensagens da tela a partir do banco.
    # O filtro por cliente_nome prova que a persistencia fica separada por cliente.
    registros = HistoricoConversa.query.filter_by(
        cliente_nome=cliente_nome
    ).order_by(
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
    # Lista todos os clientes para montar o menu lateral da tela de chat.
    clientes = Cliente.query.order_by(Cliente.id).all()
    cliente_selecionado = buscar_cliente_selecionado()

    if request.method == "POST":

        # Pega a mensagem enviada pelo formulario do template
        mensagem = request.form["mensagem"]
        cliente_nome = cliente_selecionado.nome

        # Envia a mensagem ao agente
        resposta = agent.responder(
            mensagem,
            cliente_nome=cliente_nome
        )

        # Após o agente responder, salva a troca completa no histórico
        # Permite recarregar a conversa depois e manter histórico por cliente
        registro = HistoricoConversa(
            cliente_nome=cliente_nome,
            mensagem_usuario=mensagem,
            resposta_agente=resposta
        )

        db.session.add(registro)
        db.session.commit()

    # Renderiza a interface simulada do whatsapp com o historico salvo no banco
    return render_template(
        "simulacao_venda.html",
        titulo="Atendimento",
        clientes=clientes,
        cliente_selecionado=cliente_selecionado,
        mensagens=carregar_historico(cliente_selecionado.nome)
    )


@venda_bp.route("/api/atendimento", methods=["POST"])
def atendimento_api():
    # Endpoint de teste do agente sem interface gráfica.
    # Recebe uma mensagem, chama o agente e salva a troca no histórico.
    # Aceita JSON e também form data, facilitando testes com curl/Postman/formulário.
    dados = request.get_json(silent=True) or request.form

    mensagem = dados.get("mensagem", "").strip()
    cliente_nome = dados.get("cliente_nome") or "Cliente Simulado"

    # Retorna erro 400 quando a requisição não envia texto para o agente.
    if not mensagem:
        return jsonify({"erro": "Mensagem não informada."}), 400

    resposta = agent.responder(
        mensagem,
        cliente_nome=cliente_nome
    )

    registro = HistoricoConversa(
        cliente_nome=cliente_nome,
        mensagem_usuario=mensagem,
        resposta_agente=resposta
    )

    db.session.add(registro)
    db.session.commit()

    return jsonify({
        "cliente_nome": cliente_nome,
        "mensagem_usuario": mensagem,
        "resposta_agente": resposta
    })


@venda_bp.route(
    "/vendas",
    methods=["GET", "POST"]
)
def tela_vendas():
    # mensagem e erro são enviados ao template para feedback visual após o POST.
    mensagem = None
    erro = None

    if request.method == "POST":
        # O formulário envia produto_id, quantidade e nome do cliente.
        # A conversão para int é necessária porque dados de formulário chegam como texto.
        produto_id = int(request.form["produto_id"])
        quantidade = int(request.form["quantidade"])
        cliente_nome = request.form.get("cliente_nome") or "Cliente Simulado"

        # A regra de negócio fica no service, não na rota.
        # A rota apenas recebe os dados e escolhe o que renderizar depois.
        sucesso, mensagem_retorno, venda = registrar_venda(
            produto_id,
            quantidade,
            cliente_nome=cliente_nome
        )

        if sucesso:
            mensagem = mensagem_retorno
        else:
            erro = mensagem_retorno

    # Produtos ativos alimentam o select da tela de registro de vendas.
    produtos = Produto.query.filter_by(
        ativo=True
    ).order_by(
        Produto.categoria,
        Produto.sabor
    ).all()

    # Mostra as vendas mais recentes no final da tela.
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
    # Endpoint para inspecionar as vendas sem abrir a interface gráfica.
    vendas = Venda.query.order_by(
        Venda.data_venda.desc()
    ).all()

    # Transforma objetos SQLAlchemy em dicionários serializáveis em JSON.
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
    # Permite cadastrar venda tanto via JSON quanto via form data.
    dados = request.get_json(silent=True) or request.form

    produto_id = int(dados["produto_id"])
    quantidade = int(dados["quantidade"])
    cliente_nome = dados.get("cliente_nome") or "Cliente Simulado"

    # Reutiliza a mesma regra da tela /vendas.
    sucesso, mensagem, venda = registrar_venda(
        produto_id,
        quantidade,
        cliente_nome=cliente_nome
    )

    # Erros de validação, como estoque insuficiente, retornam HTTP 400.
    if not sucesso:
        return jsonify({"erro": mensagem}), 400

    # Quando a venda é criada, retorna HTTP 201 com os dados do registro.
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
