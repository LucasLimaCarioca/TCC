"""Séries de previsão em memória a partir do contrato diário preparado.

Não lê CSV, não grava arquivos e não modifica o histórico canônico. Códigos e
metadados deste resultado continuam sujeitos à política de dados restritos.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import math
from numbers import Integral, Real
import re

import pandas as pd

from app.forecasting.errors import ForecastError


DAILY_FIELDS = frozenset({
    "data", "codigo_produto", "codigo_grupo", "categoria", "quantidade", "numero_pedidos",
})


def _day(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise ForecastError("O dataset contém data comercial inválida.")


def _text(value, maximum, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ForecastError("O dataset contém metadado textual inválido.")
    if (value != value.strip() or len(value) > maximum or (required and not value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ForecastError("O dataset contém metadado textual inválido.")
    return value or None


def _quantity(value):
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, Integral, Real)):
        raise ForecastError("O dataset contém quantidade inválida.")
    try:
        quantity = Decimal(str(value))
        numeric = float(quantity)
        if (not quantity.is_finite() or quantity < 0 or not math.isfinite(numeric)
                or (quantity != 0 and numeric == 0)):
            raise ValueError
    except (ValueError, InvalidOperation, OverflowError):
        raise ForecastError("O dataset contém quantidade inválida.") from None
    return quantity if quantity else Decimal(0), numeric


def _orders(value):
    if isinstance(value, bool):
        raise ForecastError("O dataset contém número de pedidos inválido.")
    if isinstance(value, Integral):
        number = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        number = int(value)
    else:
        raise ForecastError("O dataset contém número de pedidos inválido.")
    if not 0 <= number <= 2**63 - 1:
        raise ForecastError("O dataset contém número de pedidos inválido.")
    return number


def _quantity_text(values):
    # Soma decimal sem depender da precisão global do processo. O relatório
    # registra a quantidade excluída antes da conversão dos alvos para float64.
    values = list(values)
    if not values:
        return "0"
    maximum = max(value.adjusted() for value in values)
    minimum = min(value.as_tuple().exponent for value in values)
    with localcontext() as context:
        context.prec = max(50, maximum - minimum + len(str(len(values))) + 3)
        total = sum(values, Decimal(0))
        if total == 0:
            return "0"
        text = format(total, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text


def build_series(rows: Iterable[dict], calendar, cutoff: date, *, coverage_complete: bool) -> dict:
    """Aplica calendário/corte e preenche apenas zeros com interpretação definida.

    Dias fechados recebem zero operacional. Dias abertos sem observação somente
    recebem zero quando a exportação completa foi confirmada. Sem essa confirmação,
    uma lacuna aberta bloqueia o lote. A série de cada produto começa em sua
    primeira observação elegível; não se inventa atividade anterior à introdução.

    Observações fechadas ou futuras são excluídas só deste recorte, com prioridades
    exclusivas: primeiro futuro, depois fechado. Duplicatas diárias são sempre
    rejeitadas; a entrada já foi agregada na preparação.
    """
    if type(cutoff) is not date or type(coverage_complete) is not bool:
        raise ForecastError("Informe data de corte e completude do histórico válidas.")
    if not callable(getattr(calendar, "is_open", None)):
        raise ForecastError("Informe um calendário operacional válido.")
    groups, categories = defaultdict(set), defaultdict(set)
    group_names = defaultdict(set)
    all_codes, seen = set(), set()
    eligible = defaultdict(dict)
    excluded_quantities = {"future": [], "closed": []}
    excluded_counts = {"future": 0, "closed": 0}
    input_rows = 0

    def is_open(day):
        result = calendar.is_open(day)
        if type(result) is not bool:
            raise ForecastError("O calendário deve informar dias abertos de forma explícita.")
        return result

    for row in rows:
        input_rows += 1
        if not isinstance(row, Mapping) or set(row) != DAILY_FIELDS:
            raise ForecastError("O dataset deve conter somente as seis colunas diárias autorizadas.")
        day = _day(row["data"])
        code = _text(row["codigo_produto"], 64, required=True)
        group = _text(row["codigo_grupo"], 64)
        category = _text(row["categoria"], 100)
        quantity, numeric = _quantity(row["quantidade"])
        _orders(row["numero_pedidos"])
        if (code, day) in seen:
            raise ForecastError("O dataset contém observações diárias duplicadas.")
        seen.add((code, day))
        all_codes.add(code)
        if group:
            groups[code].add(group)
        if category:
            categories[code].add(category)
            if group:
                group_names[group].add(category)
        if day > cutoff:
            reason = "future"
        elif not is_open(day):
            reason = "closed"
        else:
            eligible[code][day] = numeric
            continue
        excluded_counts[reason] += 1
        excluded_quantities[reason].append(quantity)

    if any(len(values) > 1 for mapping in (groups, categories, group_names) for values in mapping.values()):
        raise ForecastError("O dataset contém metadados comerciais inconsistentes.")

    series, products = {}, {}
    for code, observations in sorted(eligible.items()):
        first, last = min(observations), max(observations)
        try:
            index = pd.date_range(first, cutoff, freq="D")
        except (ValueError, OverflowError):
            raise ForecastError("O intervalo do dataset não é suportado para previsão diária.") from None
        values, open_gaps, closed_days = [], 0, 0
        for timestamp in index:
            day = timestamp.date()
            if day in observations:
                values.append(observations[day])
            elif is_open(day):
                if not coverage_complete:
                    raise ForecastError("Confirme a completude do histórico antes de preencher lacunas em dias abertos.")
                open_gaps += 1
                values.append(0.0)
            else:
                closed_days += 1
                values.append(0.0)
        series[code] = pd.Series(values, index=index, dtype="float64", name=code)
        products[code] = {
            "codigo_grupo": next(iter(groups[code]), None),
            "categoria": next(iter(categories[code]), None),
            "first_observation": first.isoformat(), "last_observation": last.isoformat(),
            "series_end": cutoff.isoformat(), "observed_days": len(observations),
            "closed_days_zero_filled": closed_days, "open_days_zero_filled": open_gaps,
        }
    report = {
        "input_rows": input_rows,
        "eligible_rows": sum(len(observations) for observations in eligible.values()),
        "excluded_rows": excluded_counts,
        "excluded_quantity": {reason: _quantity_text(values) for reason, values in excluded_quantities.items()},
        "coverage_complete": coverage_complete,
        "products_without_eligible_observations": sorted(all_codes - series.keys()),
        "interval": {"start": min((entry["first_observation"] for entry in products.values()), default=None),
                     "end": cutoff.isoformat() if series else None},
        "products": products,
    }
    return {"series": series, "report": report}
