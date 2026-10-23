"""Avisos de publicação usam somente identificadores e mensagens fictícias."""

import pytest

from app.agents.protocol import (
    ONTOLOGIES, OPERATIONS, PREVISAO_PUBLICADA, ProtocolError, decode, encode, failure,
)


RUN_ID = "previsao-0123456789abcdef0123456789abcdef"


def test_publicacao_roundtrip_topologia_e_confirmacao_de_agendamento():
    assert PREVISAO_PUBLICADA in ONTOLOGIES
    assert PREVISAO_PUBLICADA in OPERATIONS["estoque"]
    assert PREVISAO_PUBLICADA not in OPERATIONS["previsao"]
    assert PREVISAO_PUBLICADA not in OPERATIONS["atendimento"]
    message = encode("estoque@localhost", PREVISAO_PUBLICADA, {"execucao_id": RUN_ID})
    request = decode(message)
    assert request.payload == {"execucao_id": RUN_ID}
    response = decode(encode("previsao@localhost", PREVISAO_PUBLICADA,
                             {"status": "agendado"}, "inform", request.thread))
    assert response.thread == request.thread
    assert response.payload == {"status": "agendado"}
    error = decode(failure("previsao@localhost", PREVISAO_PUBLICADA, request.thread,
                           "INTERNAL_ERROR", "Não foi possível agendar a avaliação."))
    assert error.performative == "failure" and error.payload["details"] == {}


@pytest.mark.parametrize("payload", [
    {}, {"execucao_id": RUN_ID, "extra": True}, {"execucao_id": None},
    {"execucao_id": True}, {"execucao_id": 1}, {"execucao_id": ""},
    {"execucao_id": "0123456789abcdef0123456789abcdef"},
    {"execucao_id": "previsao-" + "a" * 31}, {"execucao_id": "previsao-" + "a" * 33},
    {"execucao_id": "previsao-" + "A" * 32}, {"execucao_id": "previsao-" + "g" * 32},
    {"execucao_id": " " + RUN_ID}, {"execucao_id": RUN_ID + "\n"},
    {"execucao_id": "00000000-0000-0000-0000-000000000001"},
])
def test_publicacao_recusa_identificadores_ambiguos_e_campos_extras(payload):
    with pytest.raises(ProtocolError):
        encode("estoque@localhost", PREVISAO_PUBLICADA, payload)


@pytest.mark.parametrize("payload", [
    {}, {"status": "concluido"}, {"status": True},
    {"status": "agendado", "extra": RUN_ID}, {"execucao_id": RUN_ID},
])
def test_confirmacao_tem_contrato_estrito(payload):
    with pytest.raises(ProtocolError):
        encode("previsao@localhost", PREVISAO_PUBLICADA, payload, "inform")
