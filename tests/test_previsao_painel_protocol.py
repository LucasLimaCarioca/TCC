"""Contrato próprio do painel sem alterar a consulta usada pelos alertas."""

from copy import deepcopy

import pytest

from app.agents.protocol import (OPERATIONS, PREVISAO_PAINEL, ProtocolError, decode, encode)


def panel_response():
    return {"execucao_id": "previsao-" + "a" * 32, "versao": "forecast-v1",
        "produtos": [{"codigo_produto": "00100", "produto_id": None, "nome": None,
                      "codigo_grupo": "00001", "categoria": "Grupo Fictício"}],
        "selecao": {"codigo_produto": None, "granularidade": "diaria",
                    "data_inicio": "2030-01-02", "data_fim": "2030-01-02"},
        "cobertura": {"inicio": "2030-01-02", "fim": "2030-01-02", "data_corte": "2030-01-01",
                      "gerada_em": "2030-01-01T12:00:00+00:00", "desatualizada": False},
        "total_previsto": "0.1", "periodos": [{"periodo_inicio": "2030-01-02", "periodo_fim": "2030-01-02",
            "cobertura_inicio": "2030-01-02", "cobertura_fim": "2030-01-02", "parcial": False,
            "quantidade_prevista": "0.1"}], "modelos": {"naive": 1},
        "avaliacoes": [{"codigo_produto": "00100", "modelo": "naive", "fallback": True,
            "motivo": "Histórico fictício curto.", "treino_inicio": "2029-12-30", "treino_fim": "2030-01-01",
            "validacao": None, "teste_inicio": None, "teste_fim": None, "teste": None,
            "baselines": {"naive": None, "seasonal_naive": None}, "melhor_que_baselines": None}],
        "atualizacao": {"disponivel": False, "data_corte_maxima": None,
                        "data_corte_sugerida": None, "horizonte_dias": 1}}


def inform(payload):
    return encode("estoque@localhost", PREVISAO_PAINEL, payload, "inform")


def test_nova_operacao_e_isolada_e_painel_possui_schema_proprio():
    assert PREVISAO_PAINEL in OPERATIONS["previsao"]
    payload = {"codigo_produto": None, "granularidade": "diaria", "data_inicio": None, "data_fim": None}
    assert decode(encode("previsao@localhost", PREVISAO_PAINEL, payload)).payload == payload
    assert decode(inform(panel_response())).payload == panel_response()


@pytest.mark.parametrize("key,value", [
    ("codigo_produto", " 00100"), ("codigo_produto", "001 00"), ("codigo_produto", "00100\x00"),
    ("codigo_produto", "x" * 65), ("codigo_produto", 100), ("codigo_produto", ""),
    ("granularidade", "anual"), ("data_inicio", "2030-1-2"), ("data_fim", "2030-02-30"),
    ("data_inicio", "2030-01-03"), ("extra", True),
])
def test_request_rejeita_codigos_datas_e_campos_fora_do_contrato(key, value):
    payload = {"codigo_produto": "00100", "granularidade": "diaria",
               "data_inicio": "2030-01-02", "data_fim": "2030-01-02"}
    payload[key] = value
    with pytest.raises(ProtocolError):
        encode("previsao@localhost", PREVISAO_PAINEL, payload)


@pytest.mark.parametrize("path,value", [
    (("execucao_id",), "outro"), (("versao",), "outro"), (("extra",), "não permitido"),
    (("total_previsto",), "0.100"), (("total_previsto",), "0.2"), (("total_previsto",), 0.1),
    (("selecao", "codigo_produto"), "99999"), (("selecao", "data_inicio"), "2030-01-01"),
    (("selecao", "data_fim"), "2030-01-03"), (("selecao", "extra"), True),
    (("cobertura", "gerada_em"), "2030-01-01T12:00:00"), (("cobertura", "desatualizada"), 1),
    (("cobertura", "data_corte"), "2029-12-31"), (("cobertura", "extra"), True),
    (("produtos", 0, "codigo_produto"), " 00100"), (("produtos", 0, "produto_id"), True),
    (("produtos", 0, "nome"), "associação inventada"), (("produtos", 0, "categoria"), 7),
    (("produtos", 0, "extra"), True), (("periodos", 0, "parcial"), True),
    (("periodos", 0, "cobertura_fim"), "2030-01-03"), (("periodos", 0, "extra"), True),
    (("modelos", "naive"), 2), (("modelos", "naive"), True), (("modelos", "desconhecido"), 1),
    (("avaliacoes", 0, "codigo_produto"), "99999"), (("avaliacoes", 0, "modelo"), "random_forest"),
    (("avaliacoes", 0, "fallback"), 1), (("avaliacoes", 0, "teste_inicio"), "2030-01-01"),
    (("avaliacoes", 0, "melhor_que_baselines"), False), (("avaliacoes", 0, "extra"), True),
    (("avaliacoes", 0, "baselines", "extra"), None), (("atualizacao", "horizonte_dias"), 2),
    (("atualizacao", "data_corte_maxima"), "2030-01-01"), (("atualizacao", "extra"), True),
])
def test_response_valida_todos_os_niveis_e_coerencia(path, value):
    payload = panel_response()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ProtocolError):
        inform(payload)


@pytest.mark.parametrize("field", ["produtos", "avaliacoes", "periodos"])
def test_listas_nao_podem_ser_vazias_ou_duplicadas(field):
    for value in ([], [deepcopy(panel_response()[field][0])] * 2):
        payload = panel_response()
        payload[field] = value
        with pytest.raises(ProtocolError):
            inform(payload)


@pytest.mark.parametrize("frequency,start,end", [
    ("semanal", "2029-12-31", "2030-01-06"), ("mensal", "2030-01-01", "2030-01-31")])
def test_periodos_parciais_de_semana_mes_sao_preservados(frequency, start, end):
    payload = panel_response()
    payload["selecao"]["granularidade"] = frequency
    payload["periodos"][0].update(periodo_inicio=start, periodo_fim=end, parcial=True)
    assert decode(inform(payload)).payload == payload


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True, "1"])
def test_metricas_rejeitam_nao_finitos_negativos_e_tipos_errados(invalid):
    payload = panel_response()
    metric = {"mae": invalid, "rmse": 1, "wape": None, "smape": 1}
    payload["avaliacoes"][0].update(fallback=False, validacao=metric, teste=metric,
        teste_inicio="2029-12-31", teste_fim="2030-01-01",
        baselines={"naive": metric, "seasonal_naive": metric}, melhor_que_baselines=False)
    with pytest.raises((ProtocolError, ValueError)):
        inform(payload)


def test_precisao_decimal_e_indicacao_de_atualizacao():
    payload = panel_response()
    payload["total_previsto"] = payload["periodos"][0]["quantidade_prevista"] = "123456789012345678901234567890.123456789"
    payload["atualizacao"].update(disponivel=True, data_corte_maxima="2030-01-02", data_corte_sugerida="2030-01-01")
    assert decode(inform(payload)).payload == payload
    payload["atualizacao"]["data_corte_sugerida"] = "2030-01-02"
    with pytest.raises(ProtocolError):
        inform(payload)
