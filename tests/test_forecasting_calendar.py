"""Datas públicas e exceções inteiramente fictícias, sem histórico real."""

from datetime import date, datetime, timedelta
import json

import pytest

from app.forecasting.calendar import CALENDAR_VERSION, ManausCalendar
from app.forecasting.errors import ForecastError


@pytest.mark.parametrize("year,easter,carnival,good_friday,corpus", [
    (2021, "2021-04-04", "2021-02-16", "2021-04-02", "2021-06-03"),
    (2022, "2022-04-17", "2022-03-01", "2022-04-15", "2022-06-16"),
    (2023, "2023-04-09", "2023-02-21", "2023-04-07", "2023-06-08"),
    (2024, "2024-03-31", "2024-02-13", "2024-03-29", "2024-05-30"),
    (2025, "2025-04-20", "2025-03-04", "2025-04-18", "2025-06-19"),
    (2026, "2026-04-05", "2026-02-17", "2026-04-03", "2026-06-04"),
    (2027, "2027-03-28", "2027-02-09", "2027-03-26", "2027-05-27"),
])
def test_feriados_moveis_com_vigencia(year, easter, carnival, good_friday, corpus):
    calendar = ManausCalendar([year])
    for value in (good_friday, corpus):
        day = date.fromisoformat(value)
        assert not calendar.is_open(day)
        assert calendar.holiday_name(day)
    carnival_day = date.fromisoformat(carnival)
    # Carnaval não é feriado nacional; Manaus suspendeu especificamente 2021.
    assert calendar.is_open(carnival_day) is (year == 2021)
    assert bool(calendar.holiday_name(carnival_day)) is (year != 2021)
    assert not calendar.is_open(date.fromisoformat(easter))  # domingo, sem criar feriado fictício
    assert calendar.holiday_name(date.fromisoformat(easter)) is None
    assert calendar.is_open(carnival_day - timedelta(days=1))  # segunda: ponto facultativo
    ash_day = carnival_day + timedelta(days=1)
    assert calendar.is_open(ash_day)  # tarde possível, inclusive nos anos sem suspensão
    assert bool(calendar.holiday_name(ash_day)) is (year != 2021)


@pytest.mark.parametrize("value", [
    "2026-01-01", "2026-04-21", "2026-05-01", "2026-09-05", "2026-09-07",
    "2026-10-12", "2026-10-24", "2026-11-02", "2026-11-15", "2026-11-20",
    "2026-12-08", "2026-12-25", "2021-04-02", "2021-06-03", "2021-12-08",
])
def test_feriados_nacionais_estaduais_municipais_fecham(value):
    calendar = ManausCalendar([2021, 2026])
    day = date.fromisoformat(value)
    assert not calendar.is_open(day)
    assert calendar.holiday_name(day)


@pytest.mark.parametrize("value", [
    "2026-01-02", "2026-02-16", "2026-04-02", "2026-04-20", "2026-06-05",
    "2026-10-28", "2026-12-07", "2026-12-24", "2026-12-31",
])
def test_pontos_facultativos_e_emendas_nao_fecham_fabrica(value):
    calendar = ManausCalendar([2026])
    day = date.fromisoformat(value)
    assert calendar.is_open(day)
    assert calendar.holiday_name(day) is None


def test_nao_transfere_feriado_de_sabado_para_segunda():
    calendar = ManausCalendar([2026])
    assert not calendar.is_open(date(2026, 10, 24))
    assert not calendar.is_open(date(2026, 10, 25))
    assert calendar.is_open(date(2026, 10, 26))


def test_vigencia_novembro20_preserva_lei_estadual_antes_de_2024():
    calendar = ManausCalendar(range(2021, 2028))
    data = {item["date"]: item for item in calendar.metadata()["holidays"]}
    for year in range(2021, 2028):
        day = date(year, 11, 20)
        assert not calendar.is_open(day)
        assert data[day.isoformat()]["scope"] == ("state" if year < 2024 else "national")


def test_bordas_entre_anos_e_ano_bissexto():
    calendar = ManausCalendar([2024, 2025, 2026, 2027])
    assert calendar.is_open(date(2024, 2, 29))
    assert calendar.is_open(date(2025, 12, 31))
    assert not calendar.is_open(date(2026, 1, 1))
    assert calendar.is_open(date(2026, 12, 31))
    assert not calendar.is_open(date(2027, 1, 1))
    for value in (date(2023, 12, 31), date(2028, 1, 1)):
        with pytest.raises(ForecastError, match="cobertura"):
            calendar.is_open(value)
        with pytest.raises(ForecastError, match="cobertura"):
            calendar.holiday_name(value)


@pytest.mark.parametrize("years", [[], None, [2020], [2028], [True], [2026.0], ["2026"], "2026"])
def test_anos_invalidos_falham_antes_de_assumir_abertura(years):
    with pytest.raises(ForecastError):
        ManausCalendar(years)


def test_anos_nao_solicitados_falham_mesmo_dentro_do_suporte():
    calendar = ManausCalendar([2025, 2027])
    with pytest.raises(ForecastError, match="cobertura"):
        calendar.is_open(date(2026, 7, 6))


@pytest.mark.parametrize("value", [None, "2026-01-05", datetime(2026, 1, 5), 2026])
def test_dia_sem_horario_exigido(value):
    calendar = ManausCalendar([2026])
    with pytest.raises(ForecastError, match="sem horário"):
        calendar.is_open(value)


def test_excecoes_ficticias_tem_prioridade_sem_modificar_feriado_legal():
    holiday = date(2026, 1, 1)
    saturday = date(2026, 1, 3)
    workday = date(2026, 1, 5)
    supplied = {holiday: True, saturday: True, workday: False}
    calendar = ManausCalendar([2026], overrides=supplied)
    supplied.clear()
    assert calendar.is_open(holiday)
    assert calendar.holiday_name(holiday) == "Confraternização Universal"
    assert calendar.is_open(saturday)
    assert not calendar.is_open(workday)
    assert calendar.holiday_name(workday) is None
    assert calendar.metadata()["overrides"] == [
        {"date": "2026-01-01", "is_open": True},
        {"date": "2026-01-03", "is_open": True},
        {"date": "2026-01-05", "is_open": False},
    ]


@pytest.mark.parametrize("overrides", [
    [], {"2026-01-05": False}, {date(2026, 1, 5): 0}, {date(2026, 1, 5): "false"},
    {date(2025, 1, 1): False}, {datetime(2026, 1, 5): False},
])
def test_excecoes_invalidas_nao_abrem_dias_silenciosamente(overrides):
    with pytest.raises(ForecastError):
        ManausCalendar([2026], overrides=overrides)


def test_metadata_reproduzivel_serializavel_e_sem_mutacao_compartilhada():
    calendar = ManausCalendar(iter([2027, 2025, 2027]))
    first = calendar.metadata()
    assert json.loads(json.dumps(first)) == first
    assert first["version"] == CALENDAR_VERSION
    assert first["years"] == [2025, 2027]
    assert first["coverage"]["outside_coverage"] == "error"
    assert all(item["url"].startswith("https://") for item in first["sources"])
    assert first == ManausCalendar([2025, 2027]).metadata()
    first["holidays"].clear()
    first["sources"].clear()
    assert calendar.metadata()["holidays"]
    assert calendar.metadata()["sources"]
