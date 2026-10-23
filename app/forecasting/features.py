"""Features determinísticas: cada alvo depende exclusivamente do passado."""

import numpy as np
import pandas as pd

from app.forecasting.errors import ForecastError


FEATURE_COLUMNS = (
    "lag_1", "lag_7", "lag_14", "lag_28",
    "rolling_mean_7", "rolling_mean_14", "rolling_mean_28",
    "day_of_week", "week_of_year", "month", "is_weekend",
)


def validate_history(history: pd.Series, *, allow_last_nan: bool = False) -> pd.Series:
    """Valida uma série diária e devolve cópia numérica, sem corrigir a origem."""
    if not isinstance(history, pd.Series) or history.empty:
        raise ForecastError("Informe uma série diária não vazia.")
    index = history.index
    if (not isinstance(index, pd.DatetimeIndex) or index.tz is not None
            or index.hasnans or not index.is_unique or not index.is_monotonic_increasing
            or not index.equals(index.normalize())
            or not index.equals(pd.date_range(index[0], periods=len(index), freq="D"))):
        raise ForecastError("O histórico deve ter índice diário contínuo, ordenado e sem fuso.")
    if history.dtype.kind not in "iuf":
        raise ForecastError("As quantidades do histórico devem ser numéricas.")
    values = history.to_numpy(dtype="float64", copy=True)
    missing = np.isnan(values)
    if missing.any() and not (allow_last_nan and missing.sum() == 1 and missing[-1]):
        raise ForecastError("O histórico contém quantidades ausentes.")
    if np.isinf(values).any() or (values[~missing] < 0).any():
        raise ForecastError("As quantidades do histórico devem ser finitas e não negativas.")
    return pd.Series(values, index=index.copy(), name=history.name)


def make_features(history: pd.Series) -> pd.DataFrame:
    """Calcula lags e médias anteriores ao dia-alvo.

    A última quantidade pode ser NaN para representar o próximo alvo, ainda
    desconhecido. NaNs iniciais das próprias features indicam passado insuficiente
    e devem ser tratados pelo chamador antes do ajuste do modelo.
    """
    history = validate_history(history, allow_last_nan=True)
    features = pd.DataFrame(index=history.index)
    for lag in (1, 7, 14, 28):
        features[f"lag_{lag}"] = history.shift(lag)
    past = history.shift(1)
    for window in (7, 14, 28):
        features[f"rolling_mean_{window}"] = past.rolling(window, min_periods=window).mean()
    features["day_of_week"] = history.index.dayofweek
    features["week_of_year"] = history.index.isocalendar().week.to_numpy(dtype="int64")
    features["month"] = history.index.month
    features["is_weekend"] = (history.index.dayofweek >= 5).astype("int64")
    return features.loc[:, FEATURE_COLUMNS]
