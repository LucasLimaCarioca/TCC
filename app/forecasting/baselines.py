"""Referências simples de demanda, respeitando o calendário da fábrica."""

from numbers import Integral

import pandas as pd

from app.forecasting.features import validate_history


def validate_horizon(horizon):
    if isinstance(horizon, bool) or not isinstance(horizon, Integral) or horizon < 1:
        raise ValueError("O horizonte deve ser um número inteiro positivo.")
    return int(horizon)


def naive(history, horizon, calendar):
    """Repete o último valor histórico; dias futuros fechados recebem zero."""
    return _forecast(history, horizon, calendar, season=None)


def seasonal_naive(history, horizon, calendar, *, season=7):
    """Repete o último ciclo histórico, ou o último valor em séries curtas."""
    season = validate_horizon(season)
    return _forecast(history, horizon, calendar, season=season)


def _forecast(history, horizon, calendar, season):
    history = validate_history(history)
    horizon = validate_horizon(horizon)
    dates = pd.date_range(history.index[-1] + pd.DateOffset(days=1), periods=horizon, freq="D")
    pattern = history.iloc[-season:].tolist() if season is not None and len(history) >= season else [float(history.iloc[-1])]
    predicted = []
    for offset, day in enumerate(dates):
        # O calendário mascara a saída; fechamentos futuros não alteram o
        # último valor/ciclo observado que define a referência do baseline.
        value = float(pattern[offset % len(pattern)]) if calendar.is_open(day.date()) else 0.0
        predicted.append(value)
    return pd.Series(predicted, index=dates, name="quantidade_prevista", dtype=float)
