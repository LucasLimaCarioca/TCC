"""Contratos explícitos para a infraestrutura e operações futuras dos agentes."""

import json
import math
from dataclasses import dataclass
from uuid import UUID, uuid4

from spade.message import Message


PING = "infra.ping"
DISPONIBILIDADE = "estoque.disponibilidade"
BAIXA_VENDA = "estoque.baixa_venda"
PREVISAO = "previsao.consulta"
ONTOLOGIES = {PING, DISPONIBILIDADE, BAIXA_VENDA, PREVISAO}
JIDS = {name: f"{name}@localhost" for name in ("atendimento", "estoque", "previsao")}
PEERS = {"atendimento": {"estoque"}, "estoque": {"atendimento", "previsao"},
         "previsao": {"estoque"}}
OPERATIONS = {"atendimento": {PING}, "estoque": {PING, DISPONIBILIDADE, BAIXA_VENDA},
              "previsao": {PING, PREVISAO}}


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
        _require(_integer(payload["produto_id"]) and _integer(payload["horizonte_dias"]))
        _require(payload["granularidade"] in {"diaria", "semanal", "mensal"})
    elif ontology == PREVISAO:
        _require(set(payload) == {"produto_id", "granularidade", "total_previsto", "periodos", "modelo", "gerada_em"})
        _require(_integer(payload["produto_id"]) and _number(payload["total_previsto"]))
        _require(payload["granularidade"] in {"diaria", "semanal", "mensal"})
        _require(type(payload["periodos"]) is list)
        _require(isinstance(payload["modelo"], str) and isinstance(payload["gerada_em"], str))
        for period in payload["periodos"]:
            _require(type(period) is dict and set(period) == {"data", "quantidade"})
            _require(isinstance(period["data"], str) and _number(period["quantidade"]))
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
