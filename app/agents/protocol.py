"""Contratos explícitos para a infraestrutura e operações futuras dos agentes."""

import calendar
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from spade.message import Message


PING = "infra.ping"
DISPONIBILIDADE = "estoque.disponibilidade"
BAIXA_VENDA = "estoque.baixa_venda"
PREVISAO = "previsao.consulta"
PREVISAO_PAINEL = "previsao.painel"
PREVISAO_ATUALIZAR = "previsao.atualizar"
PREVISAO_STATUS = "previsao.status"
PREVISAO_PUBLICADA = "previsao.publicada"
ONTOLOGIES = {PING, DISPONIBILIDADE, BAIXA_VENDA, PREVISAO, PREVISAO_PAINEL,
              PREVISAO_ATUALIZAR, PREVISAO_STATUS, PREVISAO_PUBLICADA}
JIDS = {name: f"{name}@localhost" for name in ("atendimento", "estoque", "previsao")}
PEERS = {"atendimento": {"estoque"}, "estoque": {"atendimento", "previsao"},
         "previsao": {"estoque"}}
OPERATIONS = {"atendimento": {PING}, "estoque": {PING, DISPONIBILIDADE, BAIXA_VENDA, PREVISAO_PUBLICADA},
              "previsao": {PING, PREVISAO, PREVISAO_PAINEL, PREVISAO_ATUALIZAR, PREVISAO_STATUS}}


class ProtocolError(ValueError):
    """Mensagem inválida; nunca inclui o conteúdo recebido no erro."""


@dataclass(frozen=True)
class Envelope:
    performative: str
    ontology: str
    thread: str
    payload: dict


def _require(condition):
    if not condition:
        raise ProtocolError("Mensagem fora do contrato.")


def _integer(value, minimum=1):
    return type(value) is int and value >= minimum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _text(value, limit=128):
    return type(value) is str and 0 < len(value) <= limit and value == value.strip()


def _date(value):
    _require(type(value) is str and len(value) == 10)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ProtocolError("Mensagem fora do contrato.") from None
    _require(parsed.isoformat() == value)
    return parsed


def _uuid(value):
    _require(type(value) is str and len(value) == 36)
    try:
        _require(str(UUID(value)) == value)
    except ValueError:
        raise ProtocolError("Mensagem fora do contrato.") from None


def _quantity(value):
    # Quantidades cruzam o transporte como decimal canônico, sem conversão float.
    _require(type(value) is str and len(value) <= 64)
    _require(re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?", value) is not None)
    return Decimal(value)


def _forecast_response(payload):
    _require(set(payload) == {"produto_id", "codigo_produto", "granularidade",
        "horizonte_dias", "data_corte", "cobertura_inicio", "cobertura_fim",
        "total_previsto", "periodos", "modelo", "gerada_em", "versao", "execucao_id"}
        | ({"experimento"} if "experimento" in payload else set()))
    product = payload["produto_id"]
    _require(product is None or _integer(product))
    _require(payload["codigo_produto"] is None if product is None else _text(payload["codigo_produto"]))
    if "experimento" in payload:
        _experimental(payload["experimento"])
        associations = payload["experimento"]["associacoes"]
        if product is None:
            _require(type(payload["modelo"]) is dict and set(payload["modelo"]) ==
                     {item["codigo_produto"] for item in associations})
        else:
            _require(any(item["produto_id"] == product and item["codigo_produto"] == payload["codigo_produto"]
                         for item in associations))
    _require(payload["granularidade"] in {"diaria", "semanal", "mensal"})
    _require(_integer(payload["horizonte_dias"]) and payload["horizonte_dias"] <= 366)
    cutoff = _date(payload["data_corte"])
    start, end = _date(payload["cobertura_inicio"]), _date(payload["cobertura_fim"])
    _require(start == cutoff + timedelta(days=1))
    _require((end - start).days + 1 == payload["horizonte_dias"])
    _require(_text(payload["gerada_em"], 64))
    generated = datetime.fromisoformat(payload["gerada_em"])
    _require(generated.utcoffset() is not None)
    _require(_text(payload["versao"]) and _text(payload["execucao_id"]))
    model = payload["modelo"]
    if product is None:
        _require(type(model) is dict and 0 < len(model) <= 10000)
        _require(all(_text(code) and _text(name) for code, name in model.items()))
    else:
        _require(_text(model))
    periods = payload["periodos"]
    _require(type(periods) is list and 0 < len(periods) <= 366)
    next_day = start
    quantities = []
    for period in periods:
        _require(type(period) is dict and set(period) == {"periodo_inicio", "periodo_fim",
            "cobertura_inicio", "cobertura_fim", "parcial", "quantidade_prevista"})
        nominal_start, nominal_end = _date(period["periodo_inicio"]), _date(period["periodo_fim"])
        covered_start, covered_end = _date(period["cobertura_inicio"]), _date(period["cobertura_fim"])
        _require(nominal_start <= covered_start <= covered_end <= nominal_end)
        _require(covered_start == next_day and covered_end <= end)
        _require(covered_start == max(start, nominal_start) and covered_end == min(end, nominal_end))
        _require(type(period["parcial"]) is bool and period["parcial"] ==
                 (nominal_start != covered_start or nominal_end != covered_end))
        if payload["granularidade"] == "diaria":
            _require(nominal_start == nominal_end)
        elif payload["granularidade"] == "semanal":
            _require(nominal_start.weekday() == 0 and (nominal_end - nominal_start).days == 6)
        else:
            _require(nominal_start.day == 1 and nominal_end == date(
                nominal_start.year, nominal_start.month,
                calendar.monthrange(nominal_start.year, nominal_start.month)[1]))
        quantities.append(_quantity(period["quantidade_prevista"]))
        next_day = covered_end + timedelta(days=1)
    _require(next_day == end + timedelta(days=1))
    with localcontext() as context:
        context.prec = 128
        _require(sum(quantities, Decimal(0)) == _quantity(payload["total_previsto"]))


def _keys(value, expected):
    _require(type(value) is dict and set(value) == set(expected))


def _experimental(value):
    from app.forecasting.experimental import validate_experimental_context
    try:
        validate_experimental_context(value)
    except (ValueError, TypeError, KeyError):
        raise ProtocolError("Contexto experimental inválido.") from None


def _panel_code(value):
    return (_text(value, 64) and not any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value))


def _panel_text(value, limit):
    return _text(value, limit) and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _panel_amount(value):
    _require(type(value) is str and len(value) <= 400 and re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.[0-9]{0,8}[1-9])?", value) is not None)
    return Decimal(value)


def _panel_metric(value):
    if value is None:
        return
    _keys(value, {"mae", "rmse", "wape", "smape"})
    _require(all((key == "wape" and item is None) or _number(item)
                 for key, item in value.items()))


def _panel_request(payload):
    _keys(payload, {"codigo_produto", "granularidade", "data_inicio", "data_fim"})
    _require(payload["codigo_produto"] is None or _panel_code(payload["codigo_produto"]))
    _require(payload["granularidade"] in {"diaria", "semanal", "mensal"})
    start = None if payload["data_inicio"] is None else _date(payload["data_inicio"])
    end = None if payload["data_fim"] is None else _date(payload["data_fim"])
    if start is not None and end is not None:
        _require(0 <= (end - start).days < 366)


def _panel_response(payload):
    _keys(payload, {"execucao_id", "versao", "produtos", "selecao", "cobertura",
                    "total_previsto", "periodos", "modelos", "avaliacoes", "atualizacao"}
                    | ({"experimento"} if "experimento" in payload else set()))
    if "experimento" in payload:
        _experimental(payload["experimento"])
        _require(type(payload["produtos"]) is list and {item.get("codigo_produto") for item in payload["produtos"]}
                 == {item["codigo_produto"] for item in payload["experimento"]["associacoes"]})
        expected_products = {item["codigo_produto"]: item["produto_id"]
                             for item in payload["experimento"]["associacoes"]}
        _require(all(item.get("produto_id") == expected_products[item["codigo_produto"]]
                     for item in payload["produtos"]))
    _require(type(payload["execucao_id"]) is str and re.fullmatch(
        r"previsao-[a-f0-9]{32}", payload["execucao_id"]) is not None)
    _require(payload["versao"] == "forecast-v1")
    selection = payload["selecao"]
    _panel_request(selection)
    _require(selection["data_inicio"] is not None and selection["data_fim"] is not None)
    start, end = _date(selection["data_inicio"]), _date(selection["data_fim"])
    coverage = payload["cobertura"]
    _keys(coverage, {"inicio", "fim", "data_corte", "gerada_em", "desatualizada"})
    first, last, cutoff = _date(coverage["inicio"]), _date(coverage["fim"]), _date(coverage["data_corte"])
    _require(first == cutoff + timedelta(days=1) and 0 <= (last - first).days < 366
             and first <= start <= end <= last)
    _require(_text(coverage["gerada_em"], 40) and type(coverage["desatualizada"]) is bool)
    generated = datetime.fromisoformat(coverage["gerada_em"])
    _require(generated.utcoffset() is not None)
    products = payload["produtos"]
    _require(type(products) is list and 0 < len(products) <= 10000)
    codes = []
    for product in products:
        _keys(product, {"codigo_produto", "produto_id", "nome", "codigo_grupo", "categoria"})
        _require(_panel_code(product["codigo_produto"]))
        _require((product["produto_id"] is None and product["nome"] is None) or
                 (_integer(product["produto_id"]) and _panel_text(product["nome"], 100)))
        for field, limit in (("codigo_grupo", 64), ("categoria", 100)):
            _require(product[field] is None or _panel_text(product[field], limit))
        codes.append(product["codigo_produto"])
    _require(codes == sorted(set(codes)))
    selected = codes if selection["codigo_produto"] is None else [selection["codigo_produto"]]
    _require(set(selected) <= set(codes))
    periods = payload["periodos"]
    _require(type(periods) is list and 0 < len(periods) <= 366)
    next_day, quantities = start, []
    for period in periods:
        _keys(period, {"periodo_inicio", "periodo_fim", "cobertura_inicio", "cobertura_fim",
                       "parcial", "quantidade_prevista"})
        nominal_start, nominal_end = _date(period["periodo_inicio"]), _date(period["periodo_fim"])
        covered_start, covered_end = _date(period["cobertura_inicio"]), _date(period["cobertura_fim"])
        _require(nominal_start <= covered_start <= covered_end <= nominal_end
                 and covered_start == next_day and covered_end <= end)
        _require(covered_start == max(start, nominal_start) and covered_end == min(end, nominal_end))
        _require(type(period["parcial"]) is bool and period["parcial"] ==
                 (nominal_start != covered_start or nominal_end != covered_end))
        frequency = selection["granularidade"]
        if frequency == "diaria":
            _require(nominal_start == nominal_end)
        elif frequency == "semanal":
            _require(nominal_start.weekday() == 0 and (nominal_end - nominal_start).days == 6)
        else:
            _require(nominal_start.day == 1 and nominal_end == date(nominal_start.year,
                nominal_start.month, calendar.monthrange(nominal_start.year, nominal_start.month)[1]))
        quantities.append(_panel_amount(period["quantidade_prevista"]))
        next_day = covered_end + timedelta(days=1)
    _require(next_day == end + timedelta(days=1))
    with localcontext() as context:
        context.prec = 400
        _require(sum(quantities, Decimal(0)) == _panel_amount(payload["total_previsto"]))
    allowed_models = {"naive", "seasonal_naive", "random_forest", "hist_gradient_boosting"}
    models = payload["modelos"]
    _require(type(models) is dict and bool(models) and set(models) <= allowed_models
             and all(_integer(count) for count in models.values()) and sum(models.values()) == len(selected))
    evaluations = payload["avaliacoes"]
    _require(type(evaluations) is list and len(evaluations) == len(selected))
    observed_models, evaluated_codes = {}, []
    for evaluation in evaluations:
        _keys(evaluation, {"codigo_produto", "modelo", "fallback", "motivo", "treino_inicio", "treino_fim",
            "validacao", "teste_inicio", "teste_fim", "teste", "baselines", "melhor_que_baselines"})
        code, model = evaluation["codigo_produto"], evaluation["modelo"]
        _require(_panel_code(code) and model in allowed_models and type(evaluation["fallback"]) is bool
                 and _panel_text(evaluation["motivo"], 4000))
        _require(_date(evaluation["treino_inicio"]) <= _date(evaluation["treino_fim"]) == cutoff)
        _panel_metric(evaluation["validacao"])
        _panel_metric(evaluation["teste"])
        _keys(evaluation["baselines"], {"naive", "seasonal_naive"})
        for metric in evaluation["baselines"].values():
            _panel_metric(metric)
        if evaluation["fallback"]:
            _require(model in {"naive", "seasonal_naive"} and evaluation["validacao"] is None
                     and evaluation["teste"] is None and evaluation["teste_inicio"] is None
                     and evaluation["teste_fim"] is None and evaluation["melhor_que_baselines"] is None
                     and all(metric is None for metric in evaluation["baselines"].values()))
        else:
            _require(evaluation["validacao"] is not None and evaluation["teste"] is not None
                     and all(metric is not None for metric in evaluation["baselines"].values()))
            _require(_date(evaluation["treino_inicio"]) <= _date(evaluation["teste_inicio"]) <=
                     _date(evaluation["teste_fim"]) <= cutoff)
            better = evaluation["melhor_que_baselines"]
            _require(type(better) is bool and better == (evaluation["teste"]["mae"] <
                min(metric["mae"] for metric in evaluation["baselines"].values())))
            if model in {"naive", "seasonal_naive"}:
                _require(evaluation["teste"] == evaluation["baselines"][model])
        observed_models[model] = observed_models.get(model, 0) + 1
        evaluated_codes.append(code)
    _require(evaluated_codes == selected and observed_models == models)
    update = payload["atualizacao"]
    _keys(update, {"disponivel", "data_corte_maxima", "data_corte_sugerida", "horizonte_dias"})
    _require(type(update["disponivel"]) is bool and type(update["horizonte_dias"]) is int
             and update["horizonte_dias"] == (last - first).days + 1)
    if update["disponivel"]:
        maximum = _date(update["data_corte_maxima"])
        _require(update["data_corte_sugerida"] is None if cutoff > maximum else
                 _date(update["data_corte_sugerida"]) == cutoff)
    else:
        _require(update["data_corte_maxima"] is None and update["data_corte_sugerida"] is None)


def validate_payload(ontology, performative, payload):
    _require(type(payload) is dict)
    if performative == "failure":
        _require(set(payload) == {"code", "message", "details"})
        _require(isinstance(payload["code"], str) and bool(payload["code"]))
        _require(isinstance(payload["message"], str) and bool(payload["message"]))
        _require(type(payload["details"]) is dict)
    elif ontology == PING:
        _require(payload == {} if performative == "request" else
                 set(payload) == {"agent", "status"} and
                 payload["agent"] in JIDS and payload["status"] == "ok")
    elif ontology in {DISPONIBILIDADE, BAIXA_VENDA} and performative == "request":
        expected = {"items"} if ontology == DISPONIBILIDADE else {"items", "cliente_nome", "operacao_id"}
        _require(set(payload) == expected)
        if ontology == BAIXA_VENDA:
            _require(isinstance(payload["cliente_nome"], str) and bool(payload["cliente_nome"].strip()))
            _require(isinstance(payload["operacao_id"], str))
            UUID(payload["operacao_id"])
        _require(type(payload["items"]) is list and bool(payload["items"]))
        for item in payload["items"]:
            _require(type(item) is dict and set(item) == {"produto_id", "quantidade"})
            _require(_integer(item["produto_id"]) and _integer(item["quantidade"]))
    elif ontology == DISPONIBILIDADE:
        _require(set(payload) == {"available", "items"} and type(payload["available"]) is bool)
        _require(type(payload["items"]) is list and bool(payload["items"]))
        for item in payload["items"]:
            _require(type(item) is dict and set(item) == {"produto_id", "solicitado", "disponivel", "ok"})
            _require(_integer(item["produto_id"]) and _integer(item["solicitado"]))
            _require(_number(item["disponivel"]) and type(item["ok"]) is bool)
            _require(item["ok"] == (item["disponivel"] >= item["solicitado"]))
        _require(payload["available"] == all(item["ok"] for item in payload["items"]))
    elif ontology == BAIXA_VENDA:
        _require(set(payload) == {"success", "movimentacoes", "vendas"} and payload["success"] is True)
        _require(type(payload["movimentacoes"]) is list and bool(payload["movimentacoes"]))
        _require(all(_integer(value) for value in payload["movimentacoes"]))
        _require(type(payload["vendas"]) is list and bool(payload["vendas"]))
        _require(all(_integer(value) for value in payload["vendas"]))
        _require(len(payload["vendas"]) == len(payload["movimentacoes"]))
    elif ontology == PREVISAO and performative == "request":
        _require(set(payload) == {"produto_id", "granularidade", "horizonte_dias"})
        _require(payload["produto_id"] is None or _integer(payload["produto_id"]))
        _require(_integer(payload["horizonte_dias"]) and payload["horizonte_dias"] <= 366)
        _require(payload["granularidade"] in {"diaria", "semanal", "mensal"})
    elif ontology == PREVISAO:
        _forecast_response(payload)
    elif ontology == PREVISAO_PAINEL:
        if performative == "request":
            _panel_request(payload)
        else:
            _panel_response(payload)
    elif ontology == PREVISAO_ATUALIZAR:
        if performative == "request":
            _require(set(payload) == {"data_corte", "horizonte_dias"})
            _date(payload["data_corte"])
            _require(_integer(payload["horizonte_dias"]) and payload["horizonte_dias"] <= 366)
        else:
            _require(set(payload) == {"job_id", "status"} and payload["status"] == "em_execucao")
            _uuid(payload["job_id"])
    elif ontology == PREVISAO_PUBLICADA:
        if performative == "request":
            _require(set(payload) == {"execucao_id"})
            _require(type(payload["execucao_id"]) is str and
                     re.fullmatch(r"previsao-[0-9a-f]{32}", payload["execucao_id"]) is not None)
        else:
            _require(payload == {"status": "agendado"})
    elif ontology == PREVISAO_STATUS:
        if performative == "request":
            _require(set(payload) == {"job_id"})
            _uuid(payload["job_id"])
        else:
            _require(set(payload) == {"job_id", "status", "execucao_id", "erro"})
            _uuid(payload["job_id"])
            _require(payload["status"] in {"em_execucao", "concluido", "falhou"})
            if payload["status"] == "concluido":
                _require(_text(payload["execucao_id"]) and payload["erro"] is None)
            else:
                _require(payload["execucao_id"] is None)
                if payload["status"] == "em_execucao":
                    _require(payload["erro"] is None)
                else:
                    error = payload["erro"]
                    _require(type(error) is dict and set(error) == {"code", "message"})
                    _require(_text(error["code"], 64) and _text(error["message"], 512))
    else:
        raise ProtocolError("Ontology desconhecida.")


def decode(message):
    performative = message.get_metadata("performative")
    ontology = message.get_metadata("ontology")
    _require(performative in {"request", "inform", "failure"})
    _require(ontology in ONTOLOGIES)
    _require(message.get_metadata("language") == "application/json")
    try:
        UUID(message.thread)
        _require(type(message.body) is str and len(message.body) <= 4_000_000)
        payload = json.loads(message.body, parse_constant=lambda _: _require(False))
        validate_payload(ontology, performative, payload)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ProtocolError("Mensagem fora do contrato.") from None
    return Envelope(performative, ontology, message.thread, payload)


def encode(to, ontology, payload, performative="request", thread=None):
    message = Message(to=to, body=json.dumps(payload, ensure_ascii=False, allow_nan=False),
                      thread=thread or str(uuid4()))
    message.set_metadata("performative", performative)
    message.set_metadata("ontology", ontology)
    message.set_metadata("language", "application/json")
    decode(message)
    return message


def failure(to, ontology, thread, code, text):
    # Falhas de validação podem manter uma ontology desconhecida para correlação.
    message = Message(to=to, thread=thread, body=json.dumps(
        {"code": code, "message": text, "details": {}}))
    for key, value in {"performative": "failure", "ontology": ontology,
                       "language": "application/json"}.items():
        message.set_metadata(key, value)
    return message
