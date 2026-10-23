"""Modelos reprodutíveis com previsão recursiva, sem alvos futuros."""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from threadpoolctl import threadpool_limits

from app.forecasting.baselines import naive, seasonal_naive, validate_history, validate_horizon
from app.forecasting.features import make_features


MODEL_NAMES = ("naive", "seasonal_naive", "random_forest", "hist_gradient_boosting")
MIN_SUPERVISED_ROWS = 60


class Forecaster:
    """Treina uma série e prevê os próximos dias em ordem cronológica."""

    def __init__(self, model_name, calendar, model_params=None):
        if model_name not in MODEL_NAMES:
            raise ValueError("Modelo de previsão desconhecido.")
        if not callable(getattr(calendar, "is_open", None)):
            raise ValueError("É necessário um calendário com is_open(data).")
        self.model_name = model_name
        self.calendar = calendar
        self.model_params = dict(model_params or {})
        self.history_ = None
        self.estimator_ = None

    def fit(self, history):
        history = validate_history(history)
        self.history_ = None
        self.estimator_ = None
        if self.model_name in ("naive", "seasonal_naive"):
            if self.model_params:
                raise ValueError("Os baselines não recebem parâmetros de estimador.")
            self.history_ = history
            self.training_rows_ = len(history)
            return self
        features = make_features(history)
        usable = features.notna().all(axis=1) & np.isfinite(features).all(axis=1)
        self.training_rows_ = int(usable.sum())
        if self.training_rows_ < MIN_SUPERVISED_ROWS:
            raise ValueError("Histórico insuficiente: são necessárias 60 linhas após as defasagens.")
        if self.model_name == "random_forest":
            params = {"n_estimators": 100, "max_depth": 8, "min_samples_leaf": 2}
            params.update(self.model_params)
            params.update(random_state=42, n_jobs=1)
            estimator = RandomForestRegressor(**params)
        else:
            params = {"max_iter": 100, "max_leaf_nodes": 15, "l2_regularization": 1.0}
            params.update(self.model_params)
            params.update(random_state=42, early_stopping=False)
            estimator = HistGradientBoostingRegressor(**params)
        with threadpool_limits(limits=1):
            estimator.fit(features.loc[usable], history.loc[usable])
        self.feature_columns_ = list(features.columns)
        self.estimator_ = estimator
        self.history_ = history
        return self

    def predict(self, horizon):
        horizon = validate_horizon(horizon)
        if self.history_ is None:
            raise ValueError("O modelo deve ser ajustado antes da previsão.")
        if self.model_name == "naive":
            return naive(self.history_, horizon, self.calendar)
        if self.model_name == "seasonal_naive":
            return seasonal_naive(self.history_, horizon, self.calendar)
        dates = pd.date_range(self.history_.index[-1] + pd.DateOffset(days=1), periods=horizon, freq="D")
        known = self.history_.copy()
        values = []
        with threadpool_limits(limits=1):
            for day in dates:
                if self.calendar.is_open(day.date()):
                    candidate = pd.concat([known, pd.Series([np.nan], index=pd.DatetimeIndex([day]))])
                    row = make_features(candidate).iloc[[-1]][self.feature_columns_]
                    if not np.isfinite(row.to_numpy(dtype=float)).all():
                        raise ValueError("As características da previsão devem ser finitas.")
                    value = float(self.estimator_.predict(row)[0])
                    if not np.isfinite(value):
                        raise ValueError("O modelo produziu uma previsão não finita.")
                    value = max(0.0, value)
                else:
                    value = 0.0
                known.loc[day] = value
                values.append(value)
        return pd.Series(values, index=dates, name="quantidade_prevista", dtype=float)
