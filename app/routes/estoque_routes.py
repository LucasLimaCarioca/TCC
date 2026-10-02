"""Endpoints de estoque; validação e persistência pertencem ao serviço/agente."""

from flask import Blueprint, current_app, jsonify, redirect, request, url_for

from app.agents.base_agent import AgentUnavailable
from app.services.estoque_client import EstoqueClient
from app.services.estoque_service import EstoqueError


estoque_bp = Blueprint("estoque", __name__)


def _dados():
    dados = request.get_json(silent=True)
    if type(dados) is not dict:
        raise EstoqueError("INVALID_INPUT", "Informe um objeto JSON válido.")
    return dados


@estoque_bp.get("/estoque")
def tela_estoque():
    return redirect(url_for("produto.tela_produtos"))


@estoque_bp.get("/api/estoque")
def consultar_estoque_api():
    return jsonify(EstoqueClient().executar("visao_estoque")["produtos"])


@estoque_bp.route("/api/estoque/materias-primas", methods=["GET", "POST"])
def materias_primas_api():
    client = EstoqueClient()
    if request.method == "GET":
        return jsonify(client.executar("visao_estoque")["materias_primas"])
    dados = _dados()
    return jsonify(client.executar("cadastrar_materia_prima", codigo=dados.get("codigo"),
        nome=dados.get("nome"), unidade_medida=dados.get("unidade_medida"),
        estoque_minimo=dados.get("estoque_minimo", "0"))), 201


@estoque_bp.route("/api/estoque/movimentacoes", methods=["GET", "POST"])
def movimentacoes_api():
    client = EstoqueClient()
    if request.method == "GET":
        return jsonify(client.executar("visao_estoque")["movimentacoes"])
    dados = _dados()
    return jsonify(client.executar("movimentar", tipo_item=dados.get("tipo_item"),
        item_id=dados.get("item_id"), tipo_movimentacao=dados.get("tipo_movimentacao"),
        quantidade=dados.get("quantidade"), motivo=dados.get("motivo"),
        operacao_id=request.headers.get("Idempotency-Key") or dados.get("operacao_id"))), 201


@estoque_bp.put("/api/estoque/minimo")
def estoque_minimo_api():
    dados = _dados()
    return jsonify(EstoqueClient().executar("configurar_minimo", tipo_item=dados.get("tipo_item"),
        item_id=dados.get("item_id"), estoque_minimo=dados.get("estoque_minimo")))


@estoque_bp.get("/api/estoque/alertas")
def alertas_api():
    return jsonify(EstoqueClient().executar("visao_estoque")["alertas"])


@estoque_bp.get("/api/estoque/risco/<int:produto_id>")
def risco_api(produto_id):
    gateway = current_app.extensions.get("agent_gateway")
    if gateway is None:
        raise AgentUnavailable("Ative o runtime para consultar o agente de previsão.")
    try:
        horizonte = int(request.args.get("horizonte_dias", "7"))
    except ValueError:
        raise EstoqueError("INVALID_INPUT", "Horizonte inválido.") from None
    granularidade = request.args.get("granularidade", "diaria")
    if horizonte <= 0 or granularidade not in {"diaria", "semanal", "mensal"}:
        raise EstoqueError("INVALID_INPUT", "Granularidade ou horizonte inválidos.")
    return jsonify(gateway.consultar_risco(produto_id, granularidade, horizonte))
