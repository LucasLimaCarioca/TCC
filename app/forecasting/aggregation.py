"""Agregações coerentes sobre previsões diárias quantizadas uma única vez."""

from calendar import monthrange
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

import pandas as pd

from app.forecasting.errors import ForecastError
from app.forecasting.features import validate_history


DAILY_QUANTUM = Decimal("0.000000001")


def _text(value):
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _bounds(day, frequency):
    if frequency == "diaria":
        return day, day
    if frequency == "semanal":
        first = day - timedelta(days=day.weekday())
        return first, first + timedelta(days=6)
    first = day.replace(day=1)
    return first, day.replace(day=monthrange(day.year, day.month)[1])


def _periods(days, values, frequency):
    periods = []
    for day, value in zip(days, values):
        first, last = _bounds(day, frequency)
        if not periods or periods[-1]["periodo_inicio"] != first.isoformat():
            periods.append({
                "periodo_inicio": first.isoformat(), "periodo_fim": last.isoformat(),
                "cobertura_inicio": day.isoformat(), "cobertura_fim": day.isoformat(),
                "parcial": True, "quantidade_prevista": Decimal(0),
            })
        period = periods[-1]
        period["cobertura_fim"] = day.isoformat()
        period["quantidade_prevista"] += value
        period["parcial"] = (period["cobertura_inicio"] != period["periodo_inicio"]
                             or period["cobertura_fim"] != period["periodo_fim"])
    for period in periods:
        period["quantidade_prevista"] = _text(period["quantidade_prevista"])
    return periods


def _view(days, values):
    return {
        frequency: _periods(days, values, frequency)
        for frequency in ("diaria", "semanal", "mensal")
    } | {"total_previsto": _text(sum(values, Decimal(0)))}


def aggregate_forecasts(predictions: dict[str, pd.Series]) -> dict:
    """Deriva todas as visões dos mesmos valores diários, sem extrapolar cobertura.

    Os produtos devem compartilhar exatamente o mesmo horizonte diário. Cada
    previsão individual é arredondada uma única vez para nove casas decimais,
    com ROUND_HALF_EVEN. As demais somas usam Decimal e são devolvidas como texto
    canônico; assim geral, detalhe e frequências conservam exatamente os totais.
    """
    if not isinstance(predictions, dict) or not predictions:
        raise ForecastError("Informe previsões diárias para ao menos um produto.")
    series, index = {}, None
    for code, prediction in predictions.items():
        if (not isinstance(code, str) or not code or len(code) > 64 or code != code.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in code)):
            raise ForecastError("Informe códigos de produto textuais válidos.")
        validated = validate_history(prediction)
        if index is None:
            index = validated.index
        elif not index.equals(validated.index):
            raise ForecastError("Todos os produtos devem compartilhar o mesmo horizonte diário.")
        series[code] = validated

    # Float64 finito requer até 309 algarismos inteiros, mais nove decimais.
    # A margem também cobre o aumento de magnitude pela soma de dias/produtos.
    with localcontext() as context:
        context.prec = 350 + len(str(len(index))) + len(str(len(series)))
        context.rounding = ROUND_HALF_EVEN
        days = [timestamp.date() for timestamp in index]
        quantized = {
            code: [Decimal(str(float(value))).quantize(DAILY_QUANTUM) for value in prediction]
            for code, prediction in sorted(series.items())
        }
        overall = [sum(values, Decimal(0)) for values in zip(*quantized.values())]
        return {
            "products": {code: _view(days, values) for code, values in quantized.items()},
            "overall": _view(days, overall),
        }
