"""Snapshots de seleção fictícios atravessam os contratos F7 e do painel."""

from copy import deepcopy

import pytest

from app.agents.protocol import PREVISAO, PREVISAO_PAINEL, ProtocolError, decode, encode
from app.forecasting.experimental import context_signature
from test_previsao_protocol import response
from test_previsao_painel_protocol import panel_response


def context(code="FICTICIO-001", product=1):
    value = {"versao": 1, "natureza": "experimental", "origem_historico": "real",
        "estoque": "simulado", "vendas_operacionais": "simuladas",
        "associacoes": [{"produto_id": product, "codigo_produto": code, "versao_revisao": "a" * 64}]}
    value["assinatura"] = context_signature(value)
    return value


def forecast():
    value = response()
    value["experimento"] = context()
    return value


def panel():
    value = panel_response()
    value["produtos"][0].update(produto_id=31, nome="Produto simulado fictício")
    value["experimento"] = context("00100", 31)
    return value


def transmit(ontology, value):
    return decode(encode("estoque@localhost", ontology, value, "inform")).payload


@pytest.mark.parametrize("ontology,factory", [(PREVISAO, forecast), (PREVISAO_PAINEL, panel)])
def test_contexto_experimental_opcional_preserva_payload_e_codigos(ontology, factory):
    value = factory()
    assert transmit(ontology, value) == value
    legacy = deepcopy(value)
    del legacy["experimento"]
    assert transmit(ontology, legacy) == legacy


@pytest.mark.parametrize("ontology,factory", [(PREVISAO, forecast), (PREVISAO_PAINEL, panel)])
def test_assinatura_falsa_nao_atravessa_o_transporte(ontology, factory):
    value = factory()
    value["experimento"]["assinatura"] = "0" * 64
    with pytest.raises(ProtocolError):
        transmit(ontology, value)


@pytest.mark.parametrize("ontology,factory", [(PREVISAO, forecast), (PREVISAO_PAINEL, panel)])
@pytest.mark.parametrize("field,invalid", [("natureza", "operacional"), ("origem_historico", "simulado"),
    ("estoque", "real"), ("vendas_operacionais", "reais"), ("extra", "campo fictício não permitido")])
def test_natureza_e_schema_sao_estritos_mesmo_com_assinatura_recalculada(ontology, factory, field, invalid):
    value = factory()
    value["experimento"][field] = invalid
    value["experimento"]["assinatura"] = context_signature(value["experimento"])
    with pytest.raises(ProtocolError):
        transmit(ontology, value)


@pytest.mark.parametrize("ontology,factory", [(PREVISAO, forecast), (PREVISAO_PAINEL, panel)])
def test_codigo_previsto_precisa_pertencer_ao_snapshot(ontology, factory):
    value = factory()
    value["experimento"]["associacoes"][0]["codigo_produto"] = "000999"
    value["experimento"]["assinatura"] = context_signature(value["experimento"])
    with pytest.raises(ProtocolError):
        transmit(ontology, value)


def test_f7_id_interno_precisa_corresponder_ao_codigo_do_snapshot():
    value = forecast()
    value["produto_id"] = 7
    with pytest.raises(ProtocolError):
        transmit(PREVISAO, value)


@pytest.mark.parametrize("product,name", [(7, "Outro produto fictício"), (None, None)])
def test_painel_nao_apresenta_outro_id_ou_sem_vinculo_para_codigo_selecionado(product, name):
    value = panel()
    value["produtos"][0].update(produto_id=product, nome=name)
    with pytest.raises(ProtocolError):
        transmit(PREVISAO_PAINEL, value)


def test_f7_geral_exige_modelos_do_mesmo_conjunto_selecionado():
    value = forecast()
    value.update(produto_id=None, codigo_produto=None, modelo={"FICTICIO-001": "naive"})
    assert transmit(PREVISAO, value) == value
    value["modelo"]["CODIGO-NAO-SELECIONADO"] = "naive"
    with pytest.raises(ProtocolError):
        transmit(PREVISAO, value)


def test_codigo_com_zeros_e_id_interno_distinto_nao_sao_intercambiaveis():
    value = forecast()
    value.update(produto_id=8, codigo_produto="00042")
    value["experimento"] = context("00042", 8)
    assert transmit(PREVISAO, value)["codigo_produto"] == "00042"
    value["codigo_produto"] = "42"
    with pytest.raises(ProtocolError):
        transmit(PREVISAO, value)
