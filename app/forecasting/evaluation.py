"""Avaliação temporal: validação seleciona; teste final apenas mede."""

from numbers import Integral

import numpy as np
import pandas as pd

from app.forecasting.baselines import validate_history, validate_horizon
from app.forecasting.models import MODEL_NAMES, MIN_SUPERVISED_ROWS, Forecaster


def metrics(actual, predicted):
    """MAE/RMSE em unidades; WAPE/sMAPE em percentual, sem dividir por zero."""
    if isinstance(actual, pd.Series) and isinstance(predicted, pd.Series):
        if not actual.index.equals(predicted.index):
            raise ValueError("As datas observadas e previstas devem coincidir.")
    try:
        observed, estimated = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    except (ValueError, TypeError):
        raise ValueError("As métricas exigem vetores numéricos válidos.") from None
    if observed.ndim != 1 or estimated.ndim != 1 or observed.shape != estimated.shape or not observed.size:
        raise ValueError("As métricas exigem vetores não vazios do mesmo tamanho.")
    if not np.isfinite(observed).all() or not np.isfinite(estimated).all():
        raise ValueError("As métricas exigem valores finitos.")
    if (observed < 0).any() or (estimated < 0).any():
        raise ValueError("As métricas de demanda exigem valores não negativos.")
    error = np.abs(observed - estimated)
    total = float(observed.sum())
    denominator = observed + estimated
    relative = np.divide(error, denominator, out=np.zeros_like(error), where=denominator != 0)
    return {
        "mae": float(error.mean()),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "wape": float(error.sum() / total * 100) if total else (0.0 if not error.any() else None),
        "smape": float(200 * relative.mean()),
    }


def _date(day):
    return day.date().isoformat()


def _open_mask(index, calendar):
    return np.array([bool(calendar.is_open(day.date())) for day in index], dtype=bool)


def _params(model_params, name):
    if model_params is None:
        return None
    if not isinstance(model_params, dict) or set(model_params) - set(MODEL_NAMES):
        raise ValueError("Os parâmetros devem ser informados por nome do modelo.")
    return model_params.get(name)


def evaluate_product(history, calendar, *, horizon=28, test_days=14,
                     validation_days=14, folds=3, model_params=None):
    """Compara candidatos em rolling origin e preserva o teste final intacto.

    Cada fold utiliza somente datas anteriores à sua janela de validação.
    As métricas e a seleção usam apenas dias abertos. O modelo vencedor é
    reestimado primeiro sem o teste para medição e depois com todo o histórico
    para a previsão futura. Nenhuma métrica de teste interfere na seleção.
    """
    history = validate_history(history)
    horizon = validate_horizon(horizon)
    for value in (test_days, validation_days, folds):
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
            raise ValueError("Teste, validação e número de folds devem ser inteiros positivos.")
    for name in MODEL_NAMES:
        _params(model_params, name)
    initial_train = len(history) - test_days - validation_days * folds
    validation = {"metric": "mae", "metric_scope": "open_days", "candidates": {},
                  "selection_end": None, "test_start": None}
    result = {"selected_model": None, "selection_reason": None, "validation": validation,
              "test": None, "forecast": None, "forecaster": None, "fallback": False}
    if initial_train < 1:
        return _fallback(result, history, calendar, horizon,
                         "Histórico insuficiente para separar treino, validação temporal e teste final.")
    test = history.iloc[-test_days:]
    validation["selection_end"] = _date(history.index[-test_days - 1])
    validation["test_start"] = _date(test.index[0])
    windows = []
    for fold in range(folds):
        cut = initial_train + fold * validation_days
        train, observed = history.iloc[:cut], history.iloc[cut:cut + validation_days]
        mask = _open_mask(observed.index, calendar)
        if not mask.any():
            return _fallback(result, history, calendar, horizon,
                             "Uma janela de validação não contém dias abertos para medir desempenho.")
        windows.append((train, observed, mask))
    test_mask = _open_mask(test.index, calendar)
    if not test_mask.any():
        return _fallback(result, history, calendar, horizon,
                         "A janela de teste final não contém dias abertos para medir desempenho.")
    for name in MODEL_NAMES:
        candidate = {"metrics": None, "folds": [], "available": True, "reason": None}
        validation["candidates"][name] = candidate
        if name not in ("naive", "seasonal_naive") and initial_train - 28 < MIN_SUPERVISED_ROWS:
            candidate.update(available=False, reason="Histórico insuficiente para 60 linhas supervisionadas em todos os folds.")
            continue
        actual_parts, predicted_parts = [], []
        for train, observed, mask in windows:
            predictor = Forecaster(name, calendar, _params(model_params, name)).fit(train)
            predicted = predictor.predict(validation_days)
            actual_open, predicted_open = observed.loc[mask], predicted.loc[mask]
            candidate["folds"].append({
                "train_start": _date(train.index[0]), "train_end": _date(train.index[-1]),
                "validation_start": _date(observed.index[0]), "validation_end": _date(observed.index[-1]),
                "n_open_days": int(mask.sum()), "metrics": metrics(actual_open, predicted_open),
            })
            actual_parts.append(actual_open)
            predicted_parts.append(predicted_open)
        candidate["metrics"] = metrics(pd.concat(actual_parts), pd.concat(predicted_parts))
    available = [name for name in MODEL_NAMES if validation["candidates"][name]["available"]]
    # A ordem fixa garante que empate exato prefira o baseline mais simples.
    selected = min(available, key=lambda name: (validation["candidates"][name]["metrics"]["mae"], MODEL_NAMES.index(name)))
    result["selected_model"] = selected
    result["selection_reason"] = "Menor MAE nos dias abertos da validação temporal; empate favorece o modelo mais simples."
    test_result = {"start": _date(test.index[0]), "end": _date(test.index[-1]),
                   "n_open_days": int(test_mask.sum()), "models": {}}
    for name in dict.fromkeys((selected, "naive", "seasonal_naive")):
        predictor = Forecaster(name, calendar, _params(model_params, name)).fit(history.iloc[:-test_days])
        predicted = predictor.predict(test_days)
        test_result["models"][name] = metrics(test.loc[test_mask], predicted.loc[test_mask])
    result["test"] = test_result
    final = Forecaster(selected, calendar, _params(model_params, selected)).fit(history)
    result.update(forecaster=final, forecast=final.predict(horizon))
    return result


def _fallback(result, history, calendar, horizon, reason):
    name = "seasonal_naive" if len(history) >= 7 else "naive"
    final = Forecaster(name, calendar).fit(history)
    result.update(selected_model=name, selection_reason=reason, fallback=True,
                  forecaster=final, forecast=final.predict(horizon))
    for candidate in MODEL_NAMES:
        result["validation"]["candidates"][candidate] = {
            "metrics": None, "folds": [], "available": False, "reason": reason,
        }
    return result
