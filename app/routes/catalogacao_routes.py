"""Fluxo administrativo explícito; escritas passam pela autoridade de Estoque."""

from flask import Blueprint, jsonify, render_template, request

from app.services.catalogacao_service import CatalogacaoError, listar_catalogacao
from app.services.estoque_client import EstoqueClient


catalogacao_bp = Blueprint("catalogacao", __name__)


@catalogacao_bp.after_request
def proteger_consultas(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@catalogacao_bp.errorhandler(CatalogacaoError)
def erro_catalogacao(error):
    status = 409 if error.code in {"CATALOG_CHANGED", "MAPPING_CONFLICT", "IDEMPOTENCY_CONFLICT"} else 400
    if error.code == "STORAGE_ERROR":
        status = 503
    return {"erro": str(error), "code": error.code}, status


@catalogacao_bp.get("/catalogacao")
def tela_catalogacao():
    return render_template("catalogacao.html", titulo="Catalogação histórica")


@catalogacao_bp.get("/api/catalogacao")
def consultar_catalogacao():
    return jsonify(listar_catalogacao())


@catalogacao_bp.post("/api/catalogacao/operacoes")
def operar_catalogacao():
    data = request.get_json(silent=True)
    if type(data) is not dict or "acao" not in data:
        raise CatalogacaoError("INVALID_INPUT", "Informe uma operação de catalogação em JSON.")
    return jsonify(EstoqueClient().executar("catalogar_historico", **data))
