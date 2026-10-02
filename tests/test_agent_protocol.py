"""Contratos usam apenas payloads fictícios e rejeitam mensagens ambíguas."""

import pytest

from app.agents.config import RuntimeConfig
from app.agents.protocol import (BAIXA_VENDA, DISPONIBILIDADE, PING, PREVISAO,
                                ProtocolError, decode, encode)


@pytest.mark.parametrize(("ontology", "payload"), [
    (PING, {}),
    (DISPONIBILIDADE, {"items": [{"produto_id": 1, "quantidade": 2}]}),
    (BAIXA_VENDA, {"cliente_nome": "Cliente Fictício", "operacao_id": "00000000-0000-0000-0000-000000000001", "items": [{"produto_id": 1, "quantidade": 2}]}),
    (PREVISAO, {"produto_id": 1, "granularidade": "diaria", "horizonte_dias": 7}),
])
def test_roundtrip(ontology, payload):
    message = encode("estoque@localhost", ontology, payload)
    envelope = decode(message)
    assert envelope.payload == payload
    assert envelope.ontology == ontology
    assert envelope.thread == message.thread
    assert message.get_metadata("language") == "application/json"


@pytest.mark.parametrize(("key", "value"), [
    ("performative", "unknown"), ("ontology", "unknown"), ("language", "text/plain"),
])
def test_metadados_invalidos(key, value):
    message = encode("estoque@localhost", PING, {})
    message.set_metadata(key, value)
    with pytest.raises(ProtocolError):
        decode(message)


@pytest.mark.parametrize("body", ["{", "[]", "null", '{"x":NaN}', '{"extra":1}'])
def test_corpo_invalido(body):
    message = encode("estoque@localhost", PING, {})
    message.body = body
    with pytest.raises(ProtocolError):
        decode(message)


@pytest.mark.parametrize("quantidade", [0, -1, True, "2", 1.5])
def test_quantidade_fora_do_contrato(quantidade):
    with pytest.raises(ProtocolError):
        encode("estoque@localhost", DISPONIBILIDADE,
               {"items": [{"produto_id": 1, "quantidade": quantidade}]})


def test_thread_obrigatoria():
    message = encode("estoque@localhost", PING, {})
    message.thread = "invalida"
    with pytest.raises(ProtocolError):
        decode(message)


def test_configuracao_nao_expoe_senhas():
    passwords = {name: "segredo-ficticio" for name in ("atendimento", "estoque", "previsao")}
    assert "segredo-ficticio" not in repr(RuntimeConfig(passwords))
    with pytest.raises(ValueError):
        RuntimeConfig({})
    with pytest.raises(ValueError):
        RuntimeConfig(passwords, request_timeout=0)
