"""Previsão e avaliação usam somente séries fictícias construídas neste arquivo."""

import numpy as np
import pandas as pd
import pytest

from app.forecasting.baselines import naive, seasonal_naive
from app.forecasting.evaluation import evaluate_product, metrics
from app.forecasting.models import Forecaster


class AlwaysOpen:
    def is_open(self, day):
        return True


class Workweek:
    def is_open(self, day):
        return day.weekday() < 5


def series(days=160):
    index = pd.date_range("2024-01-01", periods=days, freq="D")
    values = np.array([float((day.weekday() + 1) * 3) if day.weekday() < 5 else 0.0 for day in index])
    return pd.Series(values, index=index)


def test_metrics_report_units_percentages_and_zero_denominators():
    measured = metrics([0, 10, 20], [0, 12, 18])
    assert measured["mae"] == pytest.approx(4 / 3)
    assert measured["rmse"] == pytest.approx(np.sqrt(8 / 3))
    assert measured["wape"] == pytest.approx(100 * 4 / 30)
    assert measured["smape"] == pytest.approx(200 * (2 / 22 + 2 / 38) / 3)
    assert metrics([0, 0], [0, 0]) == {"mae": 0.0, "rmse": 0.0, "wape": 0.0, "smape": 0.0}
    assert metrics([0, 0], [1, 2])["wape"] is None
    assert metrics([0, 0], [1, 2])["smape"] == 200.0


@pytest.mark.parametrize("actual,predicted", [([], []), ([1], [1, 2]),
    ([np.nan], [1]), ([1], [np.inf]), ([-1], [0]), ([1], [-1]), ([[1]], [[1]])])
def test_metrics_reject_invalid_vectors(actual, predicted):
    with pytest.raises(ValueError):
        metrics(actual, predicted)


def test_metrics_reject_misaligned_dates():
    with pytest.raises(ValueError, match="datas"):
        metrics(series(2), series(2).set_axis(pd.date_range("2024-01-02", periods=2)))


def test_naive_and_seasonal_recurse_without_observed_future():
    history = pd.Series(np.arange(1, 8, dtype=float), index=pd.date_range("2024-01-01", periods=7))
    assert naive(history, 9, AlwaysOpen()).tolist() == [7.0] * 9
    assert seasonal_naive(history, 15, AlwaysOpen()).tolist() == [1., 2., 3., 4., 5., 6., 7.] * 2 + [1.]
    assert seasonal_naive(history.iloc[:2], 3, AlwaysOpen()).tolist() == [2., 2., 2.]


def test_naive_closed_weekend_does_not_erase_last_observation():
    history = series(5)  # Última observação: sexta-feira fictícia, quantidade 15.
    assert naive(history, 4, Workweek()).tolist() == [0., 0., 15., 15.]


def test_seasonal_future_holiday_does_not_erase_next_weeks_pattern():
    class HolidayWorkweek(Workweek):
        def is_open(self, day):
            return super().is_open(day) and day != pd.Timestamp("2024-01-08").date()

    history = series(7)
    predicted = seasonal_naive(history, 15, HolidayWorkweek())
    assert predicted.loc["2024-01-08"] == 0.0
    assert predicted.loc["2024-01-15"] == history.loc["2024-01-01"] == 3.0
    assert predicted.loc["2024-01-22"] == 3.0


@pytest.mark.parametrize("model_name", ["naive", "seasonal_naive", "random_forest", "hist_gradient_boosting"])
def test_models_return_daily_finite_nonnegative_and_closed_zero(model_name):
    params = {"n_estimators": 8} if model_name == "random_forest" else ({"max_iter": 8} if model_name == "hist_gradient_boosting" else None)
    history = series(100)
    original = history.copy()
    model = Forecaster(model_name, Workweek(), params).fit(history)
    predicted = model.predict(18)
    pd.testing.assert_series_equal(history, original)
    assert predicted.index[0] == history.index[-1] + pd.DateOffset(days=1)
    assert predicted.index.equals(pd.date_range(predicted.index[0], periods=18))
    assert np.isfinite(predicted).all() and (predicted >= 0).all()
    assert (predicted.loc[predicted.index.weekday >= 5] == 0).all()


@pytest.mark.parametrize("kind", ["nan", "infinite", "negative", "gap", "duplicate", "reverse", "hour", "empty"])
def test_history_requires_complete_valid_nonnegative_daily_series(kind):
    history = series(6)
    if kind in ("nan", "infinite", "negative"):
        history.iloc[1] = {"nan": np.nan, "infinite": np.inf, "negative": -1}[kind]
    elif kind == "gap":
        history = history.drop(history.index[2])
    elif kind == "duplicate":
        history.index = [history.index[0]] * len(history)
    elif kind == "reverse":
        history = history.iloc[::-1]
    elif kind == "hour":
        history.index = history.index + pd.DateOffset(hours=1)
    else:
        history = history.iloc[:0]
    with pytest.raises(ValueError):
        Forecaster("naive", AlwaysOpen()).fit(history)


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5])
def test_horizon_requires_positive_integer(horizon):
    with pytest.raises(ValueError):
        Forecaster("naive", AlwaysOpen()).fit(series(6)).predict(horizon)


def test_model_and_calendar_and_fit_are_required():
    with pytest.raises(ValueError):
        Forecaster("unknown", AlwaysOpen())
    with pytest.raises(ValueError):
        Forecaster("naive", object())
    with pytest.raises(ValueError):
        Forecaster("naive", AlwaysOpen()).predict(2)


@pytest.mark.parametrize("name,params", [("random_forest", {"n_estimators": 5}), ("hist_gradient_boosting", {"max_iter": 5})])
def test_machine_learning_minimum_and_repeatability(name, params):
    with pytest.raises(ValueError, match="60 linhas"):
        Forecaster(name, Workweek(), params).fit(series(87))
    first = Forecaster(name, Workweek(), params).fit(series(88))
    second = Forecaster(name, Workweek(), params).fit(series(88))
    assert first.training_rows_ == 60
    pd.testing.assert_series_equal(first.predict(8), second.predict(8))


def test_recursive_ml_uses_its_previous_prediction_in_lag():
    class IncrementLast:
        def __init__(self):
            self.lags = []

        def predict(self, features):
            lag = float(features.iloc[0]["lag_1"])
            self.lags.append(lag)
            return [lag + 1]

    history = series(90)
    model = Forecaster("random_forest", AlwaysOpen(), {"n_estimators": 3}).fit(history)
    model.estimator_ = IncrementLast()
    predicted = model.predict(3)
    last = float(history.iloc[-1])
    assert predicted.tolist() == [last + 1, last + 2, last + 3]
    assert model.estimator_.lags == [last, last + 1, last + 2]


@pytest.mark.parametrize("output", [-5.0, np.inf])
def test_estimator_outputs_are_clipped_or_rejected(output):
    class Constant:
        def predict(self, features):
            return [output]

    model = Forecaster("random_forest", AlwaysOpen(), {"n_estimators": 3}).fit(series(90))
    model.estimator_ = Constant()
    if np.isfinite(output):
        assert model.predict(2).tolist() == [0., 0.]
    else:
        with pytest.raises(ValueError, match="não finita"):
            model.predict(2)


def test_validation_is_temporal_and_test_cannot_change_selected_model():
    history = series(100)
    first = evaluate_product(history, Workweek(), horizon=7)
    changed_test = history.copy()
    changed_test.iloc[-14:] = 10000.0
    second = evaluate_product(changed_test, Workweek(), horizon=7)
    assert first["selected_model"] == second["selected_model"]
    assert first["validation"] == second["validation"]
    assert first["test"] != second["test"]
    assert first["selected_model"] == "seasonal_naive"
    assert first["test"]["models"]["seasonal_naive"]["mae"] == 0
    previous_end = None
    for fold in first["validation"]["candidates"]["seasonal_naive"]["folds"]:
        assert fold["train_end"] < fold["validation_start"] <= fold["validation_end"] < first["test"]["start"]
        if previous_end:
            assert previous_end < fold["validation_start"]
        previous_end = fold["validation_end"]
    assert len(first["forecaster"].history_) == len(history)
    assert first["forecast"].index[0] > history.index[-1]


def test_selection_ties_prefer_simple_baseline_and_closed_days_are_excluded():
    history = series(80) * 0
    result = evaluate_product(history, Workweek(), horizon=14)
    assert result["selected_model"] == "naive"
    assert not result["fallback"]
    assert result["test"]["n_open_days"] == 10
    assert result["test"]["models"]["naive"]["wape"] == 0
    assert not result["validation"]["candidates"]["random_forest"]["available"]
    assert result["validation"]["candidates"]["random_forest"]["metrics"] is None


def test_all_candidates_use_same_validation_dates_and_past_only(monkeypatch):
    import app.forecasting.evaluation as evaluation

    fits = []

    class RecordingForecaster(Forecaster):
        def fit(self, history):
            fits.append((self.model_name, history.index[0], history.index[-1], len(history)))
            return super().fit(history)

    monkeypatch.setattr(evaluation, "Forecaster", RecordingForecaster)
    history = series(150)
    result = evaluate_product(history, Workweek(), horizon=5,
                              model_params={"random_forest": {"n_estimators": 3},
                                            "hist_gradient_boosting": {"max_iter": 3}})
    fold_ranges = []
    for candidate in result["validation"]["candidates"].values():
        assert candidate["available"]
        assert len(candidate["folds"]) == 3
        fold_ranges.append([(f["train_end"], f["validation_start"], f["validation_end"]) for f in candidate["folds"]])
    assert all(ranges == fold_ranges[0] for ranges in fold_ranges)
    assert len(fits) == 12 + len(result["test"]["models"]) + 1
    for _, _, train_end, _ in fits[:12]:
        assert train_end.date().isoformat() < result["test"]["start"]
    assert fits[-1][2] == history.index[-1]


@pytest.mark.parametrize("days,selected", [(3, "naive"), (45, "seasonal_naive")])
def test_short_history_has_explicit_fallback_without_invented_scores(days, selected):
    result = evaluate_product(series(days), Workweek(), horizon=5)
    assert result["fallback"] and result["selected_model"] == selected
    assert "insuficiente" in result["selection_reason"]
    assert result["test"] is None
    assert len(result["forecast"]) == 5
    assert all(candidate["metrics"] is None for candidate in result["validation"]["candidates"].values())


def test_no_open_validation_days_has_explicit_unmeasured_fallback():
    class Closed:
        def is_open(self, day):
            return False

    result = evaluate_product(series(80), Closed(), horizon=5)
    assert result["fallback"] and result["test"] is None
    assert "dias abertos" in result["selection_reason"]
    assert result["forecast"].tolist() == [0.] * 5


@pytest.mark.parametrize("kwargs", [{"folds": 0}, {"folds": True}, {"test_days": 1.1},
                                   {"validation_days": -2}, {"model_params": {"unknown": {}}}])
def test_invalid_evaluation_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        evaluate_product(series(90), Workweek(), **kwargs)
