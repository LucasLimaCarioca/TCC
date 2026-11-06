"""Regressão D-01/CT-32: extremos e séries normais exclusivamente fictícios."""

from decimal import Decimal, localcontext
import math
import warnings

import numpy as np
import pandas as pd
import pytest

from app.forecasting.evaluation import metrics


MAXIMUM = np.finfo(float).max
SMALLEST = float.fromhex("0x0.0000000000001p-1022")


def measured(actual, predicted):
    """Warnings numéricos e valores inválidos não passam despercebidos."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with np.errstate(all="raise"):
            result = metrics(actual, predicted)
    assert all(value is None or math.isfinite(value) for value in result.values())
    return result


def decimal_reference(actual, predicted):
    """Oracle independente das reduções NumPy e de suas escalas."""
    with localcontext() as context:
        context.prec = 2200
        observed = [Decimal.from_float(float(value)) for value in actual]
        estimated = [Decimal.from_float(float(value)) for value in predicted]
        errors = [abs(a - b) for a, b in zip(observed, estimated)]
        size, total = Decimal(len(errors)), sum(observed)
        absolute = sum(errors)
        relative = [error / (a + b) if a + b else Decimal(0)
                    for a, b, error in zip(observed, estimated, errors)]
        return {
            "mae": float(absolute / size),
            "rmse": float((sum(error * error for error in errors) / size).sqrt()),
            "wape": float(absolute / total * 100) if total else (0.0 if not absolute else None),
            "smape": float(sum(relative) / size * 200),
        }


@pytest.mark.parametrize("actual,predicted,expected", [
    pytest.param([1e308], [0], {"mae": 1e308, "rmse": 1e308, "wape": 100, "smape": 200}, id="T01-D01"),
    pytest.param([1e308, 1e308], [0, 0], {"mae": 1e308, "rmse": 1e308, "wape": 100, "smape": 200}, id="T02-somas"),
    pytest.param([1e308], [5e307], {"mae": 5e307, "rmse": 5e307, "wape": 50, "smape": 200 / 3}, id="T03-proporcional"),
    pytest.param([10, 20, 30], [10, 20, 30], {"mae": 0, "rmse": 0, "wape": 0, "smape": 0}, id="T04-perfeita"),
    pytest.param([0, 0, 0], [0, 0, 0], {"mae": 0, "rmse": 0, "wape": 0, "smape": 0}, id="T05-nulos"),
    pytest.param([0, 0], [1, 2], {"mae": 1.5, "rmse": math.sqrt(2.5), "wape": None, "smape": 200}, id="T06-wape-indefinido"),
    pytest.param([1e308], [1.5e308], {"mae": 5e307, "rmse": 5e307, "wape": 50, "smape": 40}, id="smape-soma-excessiva"),
    pytest.param([MAXIMUM] * 4, [0] * 4, {"mae": MAXIMUM, "rmse": MAXIMUM, "wape": 100, "smape": 200}, id="limite-float64"),
    pytest.param([MAXIMUM], [MAXIMUM], {"mae": 0, "rmse": 0, "wape": 0, "smape": 0}, id="perfeita-no-limite"),
])
def test_extremos_e_politicas_existentes(actual, predicted, expected):
    assert measured(actual, predicted) == pytest.approx(expected, rel=1e-14, abs=0)


@pytest.mark.parametrize("value", [1e-160, 1e-200, 1e-300, 1e-320, SMALLEST])
def test_T08_rmse_pequeno_nao_e_zerado_pelo_quadrado(value):
    result = measured([value, value], [0, 0])
    assert result == {"mae": value, "rmse": value, "wape": 100.0, "smape": 200.0}


@pytest.mark.parametrize("actual,predicted", [
    ([SMALLEST, 0], [0, 0]),
    ([1e-320, 3e-320], [0, 0]),
    ([MAXIMUM, 0], [MAXIMUM, 1e-16]),
    ([1.0, SMALLEST], [1.0, 0]),
    ([MAXIMUM, 1e-300], [0, 1e-300]),
    ([100.0], [MAXIMUM]),
    ([72.8734901867709, 89.58690524970876, 39.308822354401705],
     [1.3100417302221437e308, 1.6104976454096219e308, 7.066520008603028e307]),
    ([MAXIMUM], [float(np.nextafter(MAXIMUM, 0))]),
    ([float(np.nextafter(MAXIMUM, 0))], [math.ldexp(3.0, 970)]),
    ([SMALLEST], [MAXIMUM]),
])
def test_oracle_decimal_em_escalas_mistas_e_fronteiras(actual, predicted):
    expected = decimal_reference(actual, predicted)
    if expected["wape"] is not None and not math.isfinite(expected["wape"]):
        with pytest.raises(OverflowError, match="WAPE"):
            measured(actual, predicted)
        return
    result = measured(actual, predicted)
    for name, value in expected.items():
        if value is None:
            assert result[name] is None
        else:
            # Até dois passos subnormais; sem a tolerância absoluta usual que
            # aceitaria indevidamente RMSE=0 para qualquer valor minúsculo.
            assert result[name] == pytest.approx(value, rel=2e-14, abs=2 * math.ulp(value))
            if value > 0:
                assert result[name] > 0


def test_wape_subnormal_preserva_percentual_antes_do_arredondamento_final():
    result = measured([MAXIMUM, 0], [MAXIMUM, 1e-16])
    assert result["wape"] == decimal_reference([MAXIMUM, 0], [MAXIMUM, 1e-16])["wape"] > 0


def test_rmse_diluido_nao_perde_precisao_na_media_dos_quadrados():
    actual = np.zeros(1_000_000)
    actual[0] = 1.5e-154
    assert measured(actual, np.zeros_like(actual))["rmse"] == pytest.approx(1.5e-157, rel=1e-14, abs=0)


@pytest.mark.parametrize("actual,predicted", [([1e-308], [1e308]), ([1], [MAXIMUM]),
    ([100, 0], [MAXIMUM, MAXIMUM]), ([float(np.nextafter(100.0, 0))], [MAXIMUM])])
def test_resultado_percentual_nao_representavel_e_erro_explicito(actual, predicted):
    with pytest.raises(OverflowError, match="WAPE.*float64"):
        measured(actual, predicted)


@pytest.mark.parametrize("actual,predicted", [
    ([math.nan], [0]), ([0], [math.nan]), ([math.inf], [0]), ([0], [-math.inf]),
    ([-1], [0]), ([0], [-1]), ([1, 2], [1]), ([], []), ([[1]], [[0]]),
    ([10 ** 1000], [0]), (["invalido"], [0]),
])
def test_T07_entradas_invalidas_continuam_rejeitadas(actual, predicted):
    with pytest.raises(ValueError):
        metrics(actual, predicted)


def test_indices_temporais_devem_continuar_alinhados_e_entradas_preservadas():
    actual = pd.Series([1.0, 2.0], index=pd.date_range("2030-01-01", periods=2))
    predicted = actual.copy()
    before = actual.copy()
    measured(actual, predicted)
    pd.testing.assert_series_equal(actual, before)
    pd.testing.assert_series_equal(predicted, before)
    with pytest.raises(ValueError, match="datas"):
        metrics(actual, predicted.set_axis(pd.date_range("2030-01-02", periods=2)))


@pytest.mark.parametrize("seed", range(8))
def test_T09_equivalencia_com_formula_anterior_e_criterio_de_mae(seed):
    generator = np.random.default_rng(seed)
    actual = generator.integers(0, 2500, size=180).astype(float)
    candidates = [np.maximum(0, actual + generator.normal(0, spread, actual.size)) for spread in (1, 5, 10)]
    previous_mae, corrected_mae = [], []
    for predicted in candidates:
        error = np.abs(actual - predicted)
        denominator = actual + predicted
        old = {"mae": float(error.mean()), "rmse": float(np.sqrt(np.mean(error ** 2))),
               "wape": float(error.sum() / actual.sum() * 100),
               "smape": float(200 * np.divide(error, denominator, out=np.zeros_like(error), where=denominator != 0).mean())}
        corrected = measured(actual, predicted)
        assert corrected == pytest.approx(old, rel=1e-10, abs=1e-8)
        assert corrected["mae"] == old["mae"]
        previous_mae.append(old["mae"])
        corrected_mae.append(corrected["mae"])
    assert min(range(3), key=lambda index: (corrected_mae[index], index)) == min(
        range(3), key=lambda index: (previous_mae[index], index))
