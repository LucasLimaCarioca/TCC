"""Agregações exatas e coberturas de horizonte com previsões fictícias."""

from decimal import Decimal, localcontext

import numpy as np
import pandas as pd
import pytest

from app.forecasting.aggregation import aggregate_forecasts
from app.forecasting.errors import ForecastError


def series(values, start="2024-12-29"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"), dtype="float64")


def total(periods):
    with localcontext() as context:
        context.prec = 400
        return sum((Decimal(period["quantidade_prevista"]) for period in periods), Decimal(0))


def test_agregacoes_semana_mes_e_geral_conservam_exatamente_diaria_quantizada():
    predictions = {
        "00001": series([0.1234567896] * 10),
        "00002": series([0.3333333333] * 10),
    }
    originals = {code: values.copy(deep=True) for code, values in predictions.items()}
    result = aggregate_forecasts(predictions)
    assert result["products"]["00001"]["diaria"][0]["quantidade_prevista"] == "0.12345679"
    assert result["products"]["00002"]["diaria"][0]["quantidade_prevista"] == "0.333333333"
    assert result["overall"]["total_previsto"] == "4.56790123"
    for view in [*result["products"].values(), result["overall"]]:
        for frequency in ("diaria", "semanal", "mensal"):
            assert total(view[frequency]) == Decimal(view["total_previsto"])
    for day_index, period in enumerate(result["overall"]["diaria"]):
        assert Decimal(period["quantidade_prevista"]) == sum(
            Decimal(view["diaria"][day_index]["quantidade_prevista"])
            for view in result["products"].values()
        )
    assert Decimal(result["overall"]["total_previsto"]) == sum(
        Decimal(view["total_previsto"]) for view in result["products"].values()
    )
    for code, before in originals.items():
        pd.testing.assert_series_equal(predictions[code], before)


def test_semana_segunda_domingo_atravessa_ano_sem_separar_semana_iso():
    result = aggregate_forecasts({"001": series([1] * 10)})["overall"]
    assert result["semanal"] == [
        {"periodo_inicio": "2024-12-23", "periodo_fim": "2024-12-29",
         "cobertura_inicio": "2024-12-29", "cobertura_fim": "2024-12-29",
         "parcial": True, "quantidade_prevista": "1"},
        {"periodo_inicio": "2024-12-30", "periodo_fim": "2025-01-05",
         "cobertura_inicio": "2024-12-30", "cobertura_fim": "2025-01-05",
         "parcial": False, "quantidade_prevista": "7"},
        {"periodo_inicio": "2025-01-06", "periodo_fim": "2025-01-12",
         "cobertura_inicio": "2025-01-06", "cobertura_fim": "2025-01-07",
         "parcial": True, "quantidade_prevista": "2"},
    ]
    assert result["mensal"] == [
        {"periodo_inicio": "2024-12-01", "periodo_fim": "2024-12-31",
         "cobertura_inicio": "2024-12-29", "cobertura_fim": "2024-12-31",
         "parcial": True, "quantidade_prevista": "3"},
        {"periodo_inicio": "2025-01-01", "periodo_fim": "2025-01-31",
         "cobertura_inicio": "2025-01-01", "cobertura_fim": "2025-01-07",
         "parcial": True, "quantidade_prevista": "7"},
    ]
    assert all(period["parcial"] is False for period in result["diaria"])
    assert all(period["periodo_inicio"] == period["periodo_fim"] == period["cobertura_inicio"]
               == period["cobertura_fim"] for period in result["diaria"])


def test_mes_completo_bissexto_e_semana_parcial_nao_extrapolam_previsao():
    result = aggregate_forecasts({"001": series([1.25] * 29, start="2024-02-01")})["overall"]
    assert result["mensal"] == [{
        "periodo_inicio": "2024-02-01", "periodo_fim": "2024-02-29",
        "cobertura_inicio": "2024-02-01", "cobertura_fim": "2024-02-29",
        "parcial": False, "quantidade_prevista": "36.25",
    }]
    assert result["semanal"][0]["periodo_inicio"] == "2024-01-29"
    assert result["semanal"][0]["cobertura_inicio"] == "2024-02-01"
    assert result["semanal"][-1]["periodo_fim"] == "2024-03-03"
    assert result["semanal"][-1]["cobertura_fim"] == "2024-02-29"


def test_horizonte_de_um_dia_e_zero_quantizado():
    result = aggregate_forecasts({"001": series([0.0000000004], start="2025-01-01")})["overall"]
    assert result["total_previsto"] == "0"
    assert result["diaria"][0]["quantidade_prevista"] == "0"
    assert not result["diaria"][0]["parcial"]
    assert result["semanal"][0]["parcial"]
    assert result["mensal"][0]["parcial"]


def test_arredondamento_half_even_e_contexto_decimal_independentes():
    predictions = {"001": series([0.0000000005, 0.0000000015])}
    with localcontext() as context:
        context.prec = 2
        context.rounding = "ROUND_UP"
        result = aggregate_forecasts(predictions)["overall"]
    assert [day["quantidade_prevista"] for day in result["diaria"]] == ["0", "0.000000002"]
    assert result["total_previsto"] == "0.000000002"


def test_somas_grandes_e_pequenas_nao_perdem_parcelas_decimais():
    predictions = {"001": series([1e308]), "002": series([0.000000001])}
    result = aggregate_forecasts(predictions)
    with localcontext() as context:
        context.prec = 400
        expected = Decimal("1e308") + Decimal("0.000000001")
        assert Decimal(result["overall"]["total_previsto"]) == expected
        assert total(result["overall"]["diaria"]) == expected


@pytest.mark.parametrize("predictions", [
    {}, None, [], {1: series([1.0])}, {"": series([1.0])}, {" 001": series([1.0])},
    {"001": series([-1.0])}, {"001": series([np.nan])}, {"001": series([np.inf])},
    {"001": series([])}, {"001": pd.Series([1.0])},
    {"001": pd.Series([1.0], index=pd.date_range("2025-01-01", periods=1, tz="UTC"))},
    {"001": pd.Series([1.0, 2.0], index=pd.to_datetime(["2025-01-01", "2025-01-01"]))},
    {"001": pd.Series([1.0, 2.0], index=pd.to_datetime(["2025-01-02", "2025-01-01"]))},
    {"001": pd.Series([1.0, 2.0], index=pd.to_datetime(["2025-01-01", "2025-01-03"]))},
    {"001": series([1.0]), "002": series([1.0, 2.0])},
    {"001": series([1.0]), "002": series([1.0], start="2025-02-01")},
])
def test_rejeita_previsoes_invalidas_antes_de_agregar(predictions):
    with pytest.raises(ForecastError):
        aggregate_forecasts(predictions)
