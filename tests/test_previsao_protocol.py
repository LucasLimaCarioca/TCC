"""Contrato Fase 7 com quantidades e datas exclusivamente fictícias."""

from copy import deepcopy
from uuid import uuid4

import pytest

from app.agents.protocol import (PREVISAO, PREVISAO_ATUALIZAR, PREVISAO_STATUS,
                                 ProtocolError, decode, encode)


def response():
    return {"produto_id": 1, "codigo_produto": "FICTICIO-001", "granularidade": "diaria",
            "horizonte_dias": 1, "data_corte": "2030-01-01", "cobertura_inicio": "2030-01-02",
            "cobertura_fim": "2030-01-02", "total_previsto": "0.123456789", "periodos": [{
                "periodo_inicio": "2030-01-02", "periodo_fim": "2030-01-02",
                "cobertura_inicio": "2030-01-02", "cobertura_fim": "2030-01-02",
                "parcial": False, "quantidade_prevista": "0.123456789"}],
            "modelo": "naive", "gerada_em": "2030-01-01T10:00:00-04:00",
            "versao": "ficticia-v1", "execucao_id": "previsao-ficticia"}


def inform(ontology, payload):
    return decode(encode("estoque@localhost", ontology, payload, "inform")).payload


@pytest.mark.parametrize("product", [None, 1])
def test_previsao_geral_e_produto(product):
    payload = response()
    payload["produto_id"] = product
    if product is None:
        payload["codigo_produto"] = None
        payload["modelo"] = {"FICTICIO-001": "naive"}
    assert inform(PREVISAO, payload) == payload
    request = {"produto_id": product, "granularidade": "mensal", "horizonte_dias": 366}
    assert decode(encode("previsao@localhost", PREVISAO, request)).payload == request


@pytest.mark.parametrize(("key", "invalid"), [
    ("produto_id", True), ("produto_id", 0), ("produto_id", -1),
    ("horizonte_dias", 0), ("horizonte_dias", 367), ("horizonte_dias", True),
    ("horizonte_dias", "7"), ("granularidade", "anual"),
])
def test_rejeita_consulta_invalida(key, invalid):
    payload = {"produto_id": 1, "granularidade": "diaria", "horizonte_dias": 7}
    payload[key] = invalid
    with pytest.raises(ProtocolError):
        encode("previsao@localhost", PREVISAO, payload)


@pytest.mark.parametrize("invalid", [0.1, 0, True, "NaN", "Infinity", "-1", "-0", "01", "1.0", "1e2", ".1", "0.", " 1", "1" * 65])
def test_quantidade_exige_decimal_canonico(invalid):
    payload = response()
    payload["total_previsto"] = invalid
    with pytest.raises(ProtocolError):
        inform(PREVISAO, payload)


@pytest.mark.parametrize(("key", "invalid"), [
    ("codigo_produto", None), ("codigo_produto", ""), ("produto_id", False),
    ("gerada_em", "2030-01-01T10:00:00"), ("gerada_em", "invalida"),
    ("versao", ""), ("execucao_id", None), ("modelo", {}),
    ("data_corte", "20300101"), ("data_corte", "2030-01-02"),
    ("cobertura_inicio", "2030-01-03"), ("cobertura_fim", "2030-01-03"),
    ("horizonte_dias", 2), ("total_previsto", "0.123456788"), ("periodos", []),
])
def test_resposta_rejeita_incoerencias(key, invalid):
    payload = response()
    payload[key] = invalid
    with pytest.raises(ProtocolError):
        inform(PREVISAO, payload)


@pytest.mark.parametrize(("key", "invalid"), [
    ("periodo_inicio", "2030-01-01"), ("periodo_fim", "2030-01-03"),
    ("cobertura_inicio", "2030-01-01"), ("cobertura_fim", "2030-01-03"),
    ("parcial", True), ("parcial", 0), ("quantidade_prevista", "0.1"),
])
def test_periodos_rejeitam_sobreposicao_lacunas_e_soma(key, invalid):
    payload = response()
    payload["periodos"][0][key] = invalid
    with pytest.raises(ProtocolError):
        inform(PREVISAO, payload)


def test_schema_estrito_em_todos_os_niveis():
    for target in ("payload", "period"):
        payload = response()
        (payload if target == "payload" else payload["periodos"][0])["extra"] = "ficticio"
        with pytest.raises(ProtocolError):
            inform(PREVISAO, payload)


@pytest.mark.parametrize(("granularity", "start", "end"), [
    ("semanal", "2029-12-31", "2030-01-06"),
    ("mensal", "2030-01-01", "2030-01-31"),
])
def test_periodo_nominal_e_cobertura_parcial(granularity, start, end):
    payload = response()
    payload["granularidade"] = granularity
    payload["periodos"][0].update(periodo_inicio=start, periodo_fim=end, parcial=True)
    assert inform(PREVISAO, payload) == payload
    payload["periodos"][0]["periodo_fim"] = "2030-01-07"
    with pytest.raises(ProtocolError):
        inform(PREVISAO, payload)


def test_soma_decimal_nao_passa_por_float():
    payload = response()
    payload.update(horizonte_dias=2, cobertura_fim="2030-01-03", total_previsto="0.3")
    payload["periodos"][0]["quantidade_prevista"] = "0.1"
    second = deepcopy(payload["periodos"][0])
    second.update(periodo_inicio="2030-01-03", periodo_fim="2030-01-03",
                  cobertura_inicio="2030-01-03", cobertura_fim="2030-01-03", quantidade_prevista="0.2")
    payload["periodos"].append(second)
    assert inform(PREVISAO, payload)["total_previsto"] == "0.3"


def test_rejeita_bucket_semanal_dividido_artificialmente():
    payload = response()
    payload.update(granularidade="semanal", horizonte_dias=2, cobertura_fim="2030-01-03", total_previsto="0.3")
    first = payload["periodos"][0]
    first.update(periodo_inicio="2029-12-31", periodo_fim="2030-01-06", parcial=True, quantidade_prevista="0.1")
    second = deepcopy(first)
    second.update(cobertura_inicio="2030-01-03", cobertura_fim="2030-01-03", quantidade_prevista="0.2")
    payload["periodos"].append(second)
    with pytest.raises(ProtocolError):
        inform(PREVISAO, payload)


def test_contratos_atualizacao_e_status():
    job = str(uuid4())
    request = {"data_corte": "2030-01-01", "horizonte_dias": 28}
    assert decode(encode("previsao@localhost", PREVISAO_ATUALIZAR, request)).payload == request
    assert inform(PREVISAO_ATUALIZAR, {"job_id": job, "status": "em_execucao"})["job_id"] == job
    assert decode(encode("previsao@localhost", PREVISAO_STATUS, {"job_id": job})).payload["job_id"] == job
    for status, run_id, error in [("em_execucao", None, None), ("concluido", "previsao-ficticia", None),
        ("falhou", None, {"code": "UPDATE_FAILED", "message": "Falha fictícia."})]:
        payload = {"job_id": job, "status": status, "execucao_id": run_id, "erro": error}
        assert inform(PREVISAO_STATUS, payload) == payload


@pytest.mark.parametrize(("status", "run_id", "error"), [
    ("pendente", None, None), ("concluido", None, None), ("em_execucao", "previsao-ficticia", None),
    ("falhou", None, None), ("falhou", None, {"code": "ERROR", "message": "Ficticio", "extra": 1}),
])
def test_status_contraditorio_rejeitado(status, run_id, error):
    payload = {"job_id": str(uuid4()), "status": status, "execucao_id": run_id, "erro": error}
    with pytest.raises(ProtocolError):
        inform(PREVISAO_STATUS, payload)


@pytest.mark.parametrize("job", [None, 1, "invalido", "00000000000000000000000000000001"])
def test_job_exige_uuid_canonico(job):
    with pytest.raises(ProtocolError):
        encode("previsao@localhost", PREVISAO_STATUS, {"job_id": job})
