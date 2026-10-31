"""Interface da previsão; domínio e processamento continuam nos agentes."""

from datetime import timedelta
import logging
import re
from uuid import UUID

from flask import Blueprint, current_app, jsonify, render_template, request

from app.agents.base_agent import AgentUnavailable, RemoteFailure
from app.agents.protocol import PREVISAO_ATUALIZAR, PREVISAO_PAINEL, ProtocolError, validate_payload
from app.services.previsao_service import hoje_manaus


previsao_bp = Blueprint("previsao", __name__)


class _PrivateForecastAccess(logging.Filter):
    """Não copia códigos históricos dos filtros para o access log local."""
    def filter(self, record):
        message = record.getMessage()
        if "/api/previsoes" in message:
            record.msg = re.sub(r"(/api/previsoes[^\s?]*)\?[^\s]*", r"\1?redacted", message)
            record.args = ()
        return True


def protect_forecast_access_logs():
    logger = logging.getLogger("werkzeug")
    if not any(isinstance(item, _PrivateForecastAccess) for item in logger.filters):
        logger.addFilter(_PrivateForecastAccess())


def _gateway():
    gateway = current_app.extensions.get("agent_gateway")
    if gateway is None:
        raise AgentUnavailable("Runtime local indisponível.")
    return gateway


def _validate(ontology, payload):
    try:
        validate_payload(ontology, "request", payload)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ProtocolError("Parâmetros de previsão inválidos.") from None


@previsao_bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@previsao_bp.errorhandler(AgentUnavailable)
def unavailable(error):
    return {"erro": "O serviço de previsões está temporariamente indisponível. Tente novamente.",
            "code": "FORECAST_UNAVAILABLE"}, 503


@previsao_bp.errorhandler(ProtocolError)
def invalid(error):
    return {"erro": "Informe filtros ou parâmetros de previsão válidos.", "code": "INVALID_INPUT"}, 400


@previsao_bp.errorhandler(RemoteFailure)
def remote_failure(error):
    status = (400 if error.code in {"INVALID_INPUT", "HORIZON_UNAVAILABLE", "PERIOD_UNAVAILABLE"} else
              404 if error.code in {"HISTORY_NOT_FOUND", "PRODUCT_NOT_FOUND", "JOB_NOT_FOUND"} else
              409 if error.code in {"FORECAST_BUSY", "SELECTION_CHANGED", "SELECTION_EMPTY", "SELECTION_INVALID",
                                    "EXPERIMENTAL_RUN_REQUIRED", "PRODUCT_NOT_SELECTED", "MAPPING_REVIEW_REQUIRED"} else 503)
    return {"erro": str(error), "code": error.code}, status


@previsao_bp.errorhandler(Exception)
def unexpected(error):
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        return {"erro": "Requisição de previsão inválida.", "code": "INVALID_INPUT"}, error.code
    current_app.logger.error("Não foi possível concluir uma operação da interface de previsão.")
    return {"erro": "Não foi possível concluir a operação de previsão.", "code": "INTERNAL_ERROR"}, 503


@previsao_bp.get("/previsoes")
def tela_previsoes():
    return render_template("previsoes.html", titulo="Previsões de Demanda",
                           max_data_corte=(hoje_manaus() - timedelta(days=1)).isoformat())


@previsao_bp.get("/api/previsoes")
def consultar_painel():
    allowed = {"codigo_produto", "granularidade", "data_inicio", "data_fim"}
    if set(request.args) - allowed or any(len(values) != 1 for _, values in request.args.lists()):
        raise ProtocolError("Filtros inválidos.")
    payload = {"codigo_produto": request.args.get("codigo_produto") or None,
               "granularidade": request.args.get("granularidade", "diaria"),
               "data_inicio": request.args.get("data_inicio") or None,
               "data_fim": request.args.get("data_fim") or None}
    _validate(PREVISAO_PAINEL, payload)
    return jsonify(_gateway().consultar_painel_previsao(**payload).payload)


@previsao_bp.post("/api/previsoes/atualizar")
def atualizar_previsao():
    payload = request.get_json(silent=True)
    _validate(PREVISAO_ATUALIZAR, payload)
    return jsonify(_gateway().atualizar_previsao(**payload).payload), 202


@previsao_bp.get("/api/previsoes/tarefas/<job_id>")
def status_previsao(job_id):
    try:
        if str(UUID(job_id)) != job_id:
            raise ValueError
    except ValueError:
        raise ProtocolError("Trabalho inválido.") from None
    return jsonify(_gateway().status_previsao(job_id).payload)
