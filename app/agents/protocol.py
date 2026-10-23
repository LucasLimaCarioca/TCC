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
PREVISAO_ATUALIZAR = "previsao.atualizar"
PREVISAO_STATUS = "previsao.status"
PREVISAO_PUBLICADA = "previsao.publicada"
ONTOLOGIES = {PING, DISPONIBILIDADE, BAIXA_VENDA, PREVISAO,
              PREVISAO_ATUALIZAR, PREVISAO_STATUS, PREVISAO_PUBLICADA}
JIDS = {name: f"{name}@localhost" for name in ("atendimento", "estoque", "previsao")}
PEERS = {"atendimento": {"estoque"}, "estoque": {"atendimento", "previsao"},
         "previsao": {"estoque"}}
OPERATIONS = {"atendimento": {PING}, "estoque": {PING, DISPONIBILIDADE, BAIXA_VENDA, PREVISAO_PUBLICADA},
              "previsao": {PING, PREVISAO, PREVISAO_ATUALIZAR, PREVISAO_STATUS}}


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
        "total_previsto", "periodos", "modelo", "gerada_em", "versao", "execucao_id"})
    product = payload["produto_id"]
    _require(product is None or _integer(product))
    _require(payload["codigo_produto"] is None if product is None else _text(payload["codigo_produto"]))
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
