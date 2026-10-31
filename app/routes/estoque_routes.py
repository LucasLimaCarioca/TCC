"""Endpoints de estoque; validação e persistência pertencem ao serviço/agente."""

from flask import Blueprint, current_app, jsonify, render_template, request

from app.agents.base_agent import AgentUnavailable
from app.services.estoque_client import EstoqueClient
from app.services.estoque_service import EstoqueError, resolver_chaves_operacao
from app.services.alerta_preventivo_config import horizonte_persistente, horizonte_preventivo
from app.models.produto import Produto


estoque_bp = Blueprint("estoque", __name__)


def _dados():
    dados = request.get_json(silent=True)
    if type(dados) is not dict:
        raise EstoqueError("INVALID_INPUT", "Informe um objeto JSON válido.")
    return dados


@estoque_bp.get("/estoque")
def tela_estoque():
    estoque = EstoqueClient().executar("visao_estoque")
    produtos = Produto.query.filter_by(ativo=True).order_by(Produto.categoria, Produto.sabor).all()
    return render_template("estoque.html", titulo="Controle de Estoque", produtos=produtos, estoque=estoque,
                           tipos_vinculo={item["produto_id"]: item.get("tipo_vinculo") for item in estoque["produtos"]},
                           horizonte_preventivo_dias=horizonte_preventivo(current_app))


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
        estoque_minimo=dados.get("estoque_minimo", "0"),
        quantidade_inicial=dados.get("quantidade_inicial", "0"))), 201


@estoque_bp.route("/api/estoque/movimentacoes", methods=["GET", "POST"])
def movimentacoes_api():
    client = EstoqueClient()
    if request.method == "GET":
        return jsonify(client.executar("visao_estoque")["movimentacoes"])
    dados = _dados()
    return jsonify(client.executar("movimentar", tipo_item=dados.get("tipo_item"),
        item_id=dados.get("item_id"), tipo_movimentacao=dados.get("tipo_movimentacao"),
        quantidade=dados.get("quantidade"), motivo=dados.get("motivo"),
        operacao_id=resolver_chaves_operacao(request.headers.get("Idempotency-Key"), dados.get("operacao_id")))), 201


@estoque_bp.put("/api/estoque/minimo")
def estoque_minimo_api():
    dados = _dados()
    return jsonify(EstoqueClient().executar("configurar_minimo", tipo_item=dados.get("tipo_item"),
        item_id=dados.get("item_id"), estoque_minimo=dados.get("estoque_minimo")))


@estoque_bp.get("/api/estoque/alertas")
def alertas_api():
    return jsonify(EstoqueClient().executar("visao_estoque")["alertas"])


@estoque_bp.post("/api/estoque/alertas/sincronizar")
def sincronizar_alertas_api():
    return jsonify(EstoqueClient().executar("sincronizar_alertas"))


@estoque_bp.get("/api/estoque/risco/<int:produto_id>")
def risco_api(produto_id):
    gateway = current_app.extensions.get("agent_gateway")
    if gateway is None:
        raise AgentUnavailable("Ative o runtime para consultar o agente de previsão.")
    raw_horizonte = request.args.get("horizonte_dias")
    try:
        horizonte = int(raw_horizonte) if raw_horizonte is not None else None
    except ValueError:
        raise EstoqueError("INVALID_INPUT", "Horizonte inválido.") from None
    granularidade = request.args.get("granularidade", "diaria")
    horizonte = horizonte_preventivo(current_app, horizonte)
    if granularidade not in {"diaria", "semanal", "mensal"}:
        raise EstoqueError("INVALID_INPUT", "Granularidade ou horizonte inválidos.")
    return jsonify(gateway.consultar_risco(produto_id, granularidade, horizonte))


@estoque_bp.post("/api/estoque/alertas/preventivos/<int:produto_id>")
def sincronizar_alerta_preventivo_api(produto_id):
    dados = _dados()
    if set(dados) - {"horizonte_dias"}:
        raise EstoqueError("INVALID_INPUT", "Informe apenas o horizonte do alerta preventivo.")
    if "horizonte_dias" in dados and type(dados["horizonte_dias"]) is not int:
        raise EstoqueError("INVALID_INPUT", "Informe um horizonte inteiro entre 1 e 366 dias.")
    horizonte = horizonte_persistente(current_app, dados.get("horizonte_dias"))
    gateway = current_app.extensions.get("agent_gateway")
    if gateway is None:
        raise AgentUnavailable("Ative o runtime para atualizar os alertas preventivos.")
    return jsonify(gateway.sincronizar_alerta_preventivo(produto_id, horizonte))
