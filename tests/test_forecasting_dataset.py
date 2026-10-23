"""Séries e features sobre exemplos fictícios, sem acesso ao histórico restrito."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.forecasting.dataset import build_series
from app.forecasting.errors import ForecastError
from app.forecasting.features import FEATURE_COLUMNS, make_features, validate_history


class Calendar:
    """Calendário de teste independente dos feriados reais."""

    def is_open(self, day):
        return day.weekday() < 5 and day != date(2025, 3, 5)


def row(**changes):
    result = {
        "data": "2025-03-03", "codigo_produto": "00001", "codigo_grupo": "00007",
        "categoria": "Categoria Fictícia", "quantidade": "2.5", "numero_pedidos": "1",
    }
    result.update(changes)
    return result


def build(rows, cutoff=date(2025, 3, 10), *, coverage=True):
    return build_series(rows, Calendar(), cutoff, coverage_complete=coverage)


def history(size=40):
    return pd.Series(np.arange(size, dtype="float64"), index=pd.date_range("2025-01-01", periods=size, freq="D"))


def test_serie_calendario_completo_preserva_origem_codigos_e_metadados():
    rows = [row(data="2025-03-06", quantidade=Decimal("4.75"), numero_pedidos=2), row()]
    before = deepcopy(rows)
    result = build(iter(rows))
    assert rows == before
    serie = result["series"]["00001"]
    assert serie.index.equals(pd.date_range("2025-03-03", "2025-03-10", freq="D"))
    assert serie.dtype == "float64"
    assert serie.tolist() == [2.5, 0, 0, 4.75, 0, 0, 0, 0]
    assert serie.sum() == 7.25  # Entrada já diária: não multiplicar pelo número de pedidos.
    report = result["report"]
    assert report["input_rows"] == report["eligible_rows"] == 2
    assert report["interval"] == {"start": "2025-03-03", "end": "2025-03-10"}
    assert report["products"]["00001"] == {
        "codigo_grupo": "00007", "categoria": "Categoria Fictícia",
        "first_observation": "2025-03-03", "last_observation": "2025-03-06",
        "series_end": "2025-03-10", "observed_days": 2,
        "closed_days_zero_filled": 3, "open_days_zero_filled": 3,
    }


def test_cada_produto_comeca_na_primeira_observacao_elegivel():
    result = build([row(data="2025-03-01", quantidade="900"), row(),
                    row(data="2025-03-07", codigo_produto="00002")])
    assert result["series"]["00001"].index[0] == pd.Timestamp("2025-03-03")
    assert result["series"]["00002"].index[0] == pd.Timestamp("2025-03-07")
    assert result["report"]["products"]["00002"]["open_days_zero_filled"] == 1


def test_exclui_fechados_e_futuros_com_motivos_exclusivos_e_quantidade_decimal():
    result = build([
        row(), row(data="2025-03-05", quantidade="0.1"),
        row(data="2025-03-08", quantidade="0.2"),
        row(data="2025-03-15", codigo_produto="00002", quantidade="0.3"),
    ])
    report = result["report"]
    assert report["input_rows"] == 4
    assert report["eligible_rows"] == 1
    assert report["excluded_rows"] == {"future": 1, "closed": 2}
    assert report["excluded_quantity"] == {"future": "0.3", "closed": "0.3"}
    assert report["products_without_eligible_observations"] == ["00002"]
    assert report["input_rows"] == report["eligible_rows"] + sum(report["excluded_rows"].values())
    assert result["series"]["00001"].loc["2025-03-05"] == 0


def test_sem_observacoes_elegiveis_gera_relatorio_sem_inventar_serie():
    result = build([row(data="2025-03-08")])
    assert result["series"] == {}
    assert result["report"]["products_without_eligible_observations"] == ["00001"]
    assert result["report"]["interval"] == {"start": None, "end": None}
    empty = build([])
    assert empty["series"] == {}
    assert empty["report"]["input_rows"] == 0


def test_nao_preenche_lacuna_aberta_sem_completude_confirmada():
    with pytest.raises(ForecastError, match="completude"):
        build([row()], coverage=False)
    # Um dia fechado é zero operacional mesmo sem confirmação da exportação.
    result = build([row(data="2025-03-07")], cutoff=date(2025, 3, 9), coverage=False)
    assert result["series"]["00001"].tolist() == [2.5, 0, 0]
    assert result["report"]["products"]["00001"]["closed_days_zero_filled"] == 2


def test_metadados_opcionais_vazios_sao_normalizados_sem_nova_chave():
    result = build([row(codigo_grupo=None, categoria=""), row(data="2025-03-04")])
    assert list(result["series"]) == ["00001"]
    assert result["report"]["products"]["00001"]["categoria"] == "Categoria Fictícia"
    absent = build([row(codigo_grupo="", categoria=None)])
    assert absent["report"]["products"]["00001"]["codigo_grupo"] is None
    assert absent["report"]["products"]["00001"]["categoria"] is None


@pytest.mark.parametrize("changes", [
    {"codigo_grupo": "outro"}, {"categoria": "Outra categoria fictícia"}, {},
])
def test_rejeita_duplicatas_antes_de_somar_ou_substituir(changes):
    with pytest.raises(ForecastError, match="duplicadas"):
        build([row(), row(**changes)])


def test_rejeita_duplicata_tambem_em_dias_excluidos():
    with pytest.raises(ForecastError, match="duplicadas"):
        build([row(data="2025-03-08"), row(data="2025-03-08")])


@pytest.mark.parametrize("changes", [
    {"codigo_grupo": "outro"}, {"categoria": "Outra categoria fictícia"},
    {"codigo_produto": "00002", "categoria": "Outra categoria fictícia"},
])
def test_rejeita_metadados_comerciais_divergentes(changes):
    with pytest.raises(ForecastError, match="inconsistentes"):
        build([row(), row(data="2025-03-04", **changes)])


@pytest.mark.parametrize("changes", [
    {"cliente": "PESSOA_FICTICIA_NAO_EXIBIR"}, {"preco": "8"},
    {"data": "2025-02-30"}, {"data": "20250303"}, {"data": "2025-03-03T00:00:00+00:00"},
    {"data": datetime(2025, 3, 3)}, {"codigo_produto": 1}, {"codigo_produto": ""},
    {"codigo_produto": "x" * 65}, {"codigo_produto": " 001"}, {"codigo_grupo": "x" * 65},
    {"categoria": "x" * 101}, {"categoria": "oculta\nquebra"},
    {"quantidade": "-1"}, {"quantidade": "NaN"}, {"quantidade": "Infinity"},
    {"quantidade": "1e999"}, {"quantidade": "1e-999"}, {"quantidade": True},
    {"numero_pedidos": "-1"}, {"numero_pedidos": "1.5"}, {"numero_pedidos": True},
    {"numero_pedidos": 2**63},
])
def test_schema_fechado_e_valores_invalidos_nao_exibem_conteudo(changes):
    with pytest.raises(ForecastError) as error:
        build([row(**changes)])
    assert "PESSOA_FICTICIA_NAO_EXIBIR" not in str(error.value)
    assert "oculta" not in str(error.value)


def test_schema_incompleto_e_parametros_invalidos_rejeitados():
    values = row()
    del values["categoria"]
    with pytest.raises(ForecastError, match="seis colunas"):
        build([values])
    for cutoff, coverage in [("2025-03-10", True), (datetime(2025, 3, 10), True), (date(2025, 3, 10), 1)]:
        with pytest.raises(ForecastError):
            build_series([row()], Calendar(), cutoff, coverage_complete=coverage)
    with pytest.raises(ForecastError, match="calendário"):
        build_series([row()], None, date(2025, 3, 10), coverage_complete=True)


def test_features_lags_e_medias_olham_apenas_o_passado():
    source = history()
    before = source.copy(deep=True)
    result = make_features(source)
    pd.testing.assert_series_equal(source, before)
    assert result.index.equals(source.index)
    assert tuple(result.columns) == FEATURE_COLUMNS
    for lag in (1, 7, 14, 28):
        assert result.iloc[28][f"lag_{lag}"] == source.iloc[28 - lag]
        assert result[f"lag_{lag}"].iloc[:lag].isna().all()
    for window in (7, 14, 28):
        assert result.iloc[28][f"rolling_mean_{window}"] == source.iloc[28-window:28].mean()
        assert result[f"rolling_mean_{window}"].iloc[:window].isna().all()
    mutated = source.copy()
    mutated.iloc[28:] = 99999
    pd.testing.assert_frame_equal(make_features(source).iloc[:29], make_features(mutated).iloc[:29])


def test_placeholder_final_desconhecido_nao_altera_features_do_alvo():
    source = history()
    expected = make_features(source)
    source.iloc[-1] = np.nan
    pd.testing.assert_frame_equal(make_features(source), expected)
    assert make_features(source).iloc[-1].notna().all()
    # Um alvo sem qualquer passado pode existir, mas não tem features de lag.
    assert make_features(pd.Series([np.nan], index=pd.date_range("2025-01-01", periods=1)))["lag_1"].isna().all()


def test_features_calendario_usam_semana_iso_dia_mes_e_fim_de_semana():
    result = make_features(pd.Series([2.0] * 5, index=pd.date_range("2024-12-29", periods=5)))
    assert result["week_of_year"].tolist() == [52, 1, 1, 1, 1]
    assert result["month"].tolist() == [12, 12, 12, 1, 1]
    assert result["day_of_week"].tolist() == [6, 0, 1, 2, 3]
    assert result["is_weekend"].tolist() == [1, 0, 0, 0, 0]


@pytest.mark.parametrize("source", [
    pd.Series(dtype="float64"), pd.Series([1.0, 2.0]),
    pd.Series([1.0, 2.0], index=pd.to_datetime(["2025-01-01", "2025-01-01"])),
    pd.Series([1.0, 2.0], index=pd.to_datetime(["2025-01-02", "2025-01-01"])),
    pd.Series([1.0, 2.0], index=pd.to_datetime(["2025-01-01", "2025-01-03"])),
    pd.Series([1.0], index=pd.to_datetime(["2025-01-01T01:00:00"])),
    pd.Series([1.0], index=pd.date_range("2025-01-01", periods=1, tz="UTC")),
    pd.Series([1.0], index=pd.DatetimeIndex([pd.NaT])),
    pd.Series([True], index=pd.date_range("2025-01-01", periods=1)),
    pd.Series(["1"], index=pd.date_range("2025-01-01", periods=1)),
    pd.Series([1j], index=pd.date_range("2025-01-01", periods=1)),
    pd.Series([-1.0], index=pd.date_range("2025-01-01", periods=1)),
    pd.Series([np.inf], index=pd.date_range("2025-01-01", periods=1)),
    pd.Series([np.nan, 1.0], index=pd.date_range("2025-01-01", periods=2)),
    pd.Series([np.nan, np.nan], index=pd.date_range("2025-01-01", periods=2)),
])
def test_features_rejeitam_historico_malformado(source):
    with pytest.raises(ForecastError):
        make_features(source)


def test_validador_numerico_nao_aceita_placeholder_por_padrao():
    source = history()
    source.iloc[-1] = np.nan
    with pytest.raises(ForecastError, match="ausentes"):
        validate_history(source)
