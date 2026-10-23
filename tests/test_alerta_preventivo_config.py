"""Configurações operacionais independentes de dados históricos."""

from types import SimpleNamespace

import pytest

from app.services.alerta_preventivo_config import automatico_ativo, horizonte_preventivo
from app.services.estoque_service import EstoqueError


def test_padrao_sete_dias_e_auto_ativo_apenas_em_producao(monkeypatch):
    monkeypatch.delenv("PREDICTIVE_ALERT_HORIZON_DAYS", raising=False)
    monkeypatch.delenv("PREDICTIVE_ALERTS_AUTO_ENABLED", raising=False)
    assert horizonte_preventivo(SimpleNamespace(config={}, testing=False)) == 7
    assert automatico_ativo(SimpleNamespace(config={}, testing=False))
    assert not automatico_ativo(SimpleNamespace(config={}, testing=True))


def test_config_producao_le_ambiente_e_testes_nao_herdam(monkeypatch):
    monkeypatch.setenv("PREDICTIVE_ALERT_HORIZON_DAYS", "14")
    monkeypatch.setenv("PREDICTIVE_ALERTS_AUTO_ENABLED", "1")
    assert horizonte_preventivo(SimpleNamespace(config={}, testing=False)) == 14
    assert horizonte_preventivo(SimpleNamespace(config={}, testing=True)) == 7
    assert not automatico_ativo(SimpleNamespace(config={}, testing=True))


@pytest.mark.parametrize("value", [False, 0, 367, 1.5, "NaN", "", "sete"])
def test_rejeita_config_horizonte_invalido(value):
    with pytest.raises(EstoqueError):
        horizonte_preventivo(SimpleNamespace(config={"PREDICTIVE_ALERT_HORIZON_DAYS": value}, testing=True))


@pytest.mark.parametrize("value", [0, True, "7", 367, -1])
def test_override_da_consulta_exige_inteiro(value):
    with pytest.raises(EstoqueError):
        horizonte_preventivo(SimpleNamespace(config={}, testing=True), value)


@pytest.mark.parametrize("value", [None, 1, "true", "yes", ""])
def test_rejeita_flag_automatica_ambigua(value):
    with pytest.raises(EstoqueError):
        automatico_ativo(SimpleNamespace(config={"PREDICTIVE_ALERTS_AUTO_ENABLED": value}, testing=True))
