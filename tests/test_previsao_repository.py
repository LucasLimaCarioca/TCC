"""Publicação de previsões apenas com produtos e quantidades fictícios."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import threading

import pandas as pd
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.database import db
from app.forecasting.aggregation import aggregate_forecasts
from app.models import PrevisaoDemanda, PrevisaoExecucao, Produto
from app.services.previsao_repository import (
    RepositoryError, carregar_ativa, persistir_execucao, validar_execucao,
)


def bundle(number=1, *, codes=("00101", "00102"), horizon=3):
    run_id = "previsao-" + format(number, "032x")
    cutoff = date(2025, 1, 30)
    predictions = {code: pd.Series([0.1 * (step + 1) for step in range(horizon)],
                  index=pd.date_range(cutoff + timedelta(days=1), periods=horizon)) for code in codes}
    result = {"version": "forecast-v1", "run_id": run_id, "generated_at": "2025-01-31T12:00:00+00:00",
              "cutoff": cutoff.isoformat(), "horizon_days": horizon,
              "forecast": aggregate_forecasts(predictions), "models": {code: "naive" for code in codes}}
    report = {key: result[key] for key in ("version", "run_id", "generated_at", "cutoff", "horizon_days")}
    report["evaluations"] = {code: {
        "selected_model": "naive", "selection_reason": "Histórico fictício curto.", "fallback": True,
        "validation": {"metric": "mae", "metric_scope": "open_days", "selection_end": None, "test_start": None,
                       "candidates": {name: {"metrics": None, "folds": [], "available": False,
                                              "reason": "Histórico fictício curto."} for name in
                                      ("naive", "seasonal_naive", "random_forest", "hist_gradient_boosting")}},
        "test": None, "test_comparison": {"status": "nao_avaliado", "reason": "Histórico fictício curto."},
        "model_parameters": {},
    } for code in codes}
    report["dataset"] = {"campo_livre_ficticio": "não armazenar este campo"}
    return {"manifest_path": f"data/artifacts/{run_id}/manifest.json", "manifest_sha256": "a" * 64,
            "result": result, "report": report}


def test_banco_sem_publicacao_retorna_none(app):
    with app.app_context():
        assert carregar_ativa() is None


def test_publicacao_preserva_codigos_sem_catalogo_e_decimal_exato(app):
    source = bundle()
    with app.app_context():
        assert validar_execucao(source["result"], source["report"]) is None
        assert persistir_execucao(**source) == {"run_id": source["result"]["run_id"], "status": "concluido"}
        execution = db.session.get(PrevisaoExecucao, source["result"]["run_id"])
        rows = db.session.scalars(select(PrevisaoDemanda)).all()
        assert {row.codigo_produto for row in rows} == {"00101", "00102"}
        assert all(row.produto_id is None and row.metricas_json is None for row in rows)
        assert len(rows) == sum(len(view[frequency]) for view in source["result"]["forecast"]["products"].values()
                                for frequency in ("diaria", "semanal", "mensal"))
        assert execution.ativa is True
        assert execution.metricas_json == source["report"]["evaluations"]
        assert "dataset" not in execution.metadados_json
        loaded = carregar_ativa()
        assert loaded["result"] == source["result"]
        assert loaded["manifest_sha256"] == source["manifest_sha256"]
        assert loaded["report"] == {key: value for key, value in source["report"].items() if key != "dataset"}
        for row in rows:
            view = source["result"]["forecast"]["products"][row.codigo_produto][row.granularidade]
            expected = next(period for period in view if period["periodo_inicio"] == row.periodo_inicio.isoformat())
            assert row.quantidade_exata == expected["quantidade_prevista"]
            assert row.cobertura_inicio.isoformat() == expected["cobertura_inicio"]
            assert row.cobertura_fim.isoformat() == expected["cobertura_fim"]


def test_associacao_apenas_por_codigo_externo_exato_e_snapshot_nao_muda(app):
    source = bundle()
    with app.app_context():
        mapped = Produto(nome="Produto Fictício Mapeado", codigo_externo="00101", preco=1)
        misleading = Produto(nome="00102", codigo_externo="102", preco=1)
        db.session.add_all([mapped, misleading])
        db.session.commit()
        persistir_execucao(**source)
        rows = db.session.scalars(select(PrevisaoDemanda)).all()
        assert all(row.produto_id == mapped.id for row in rows if row.codigo_produto == "00101")
        assert all(row.produto_id is None for row in rows if row.codigo_produto == "00102")
        misleading.codigo_externo = "00102"
        db.session.commit()
        persistir_execucao(**source)
        assert all(row.produto_id is None for row in db.session.scalars(
            select(PrevisaoDemanda).where(PrevisaoDemanda.codigo_produto == "00102")))


def test_precisao_textual_nao_depende_do_float_sqlite(app):
    source = bundle(codes=("00001",), horizon=1)
    exact = "123456789012345.123456789"
    for view in (source["result"]["forecast"]["products"]["00001"], source["result"]["forecast"]["overall"]):
        view["total_previsto"] = exact
        for frequency in ("diaria", "semanal", "mensal"):
            view[frequency][0]["quantidade_prevista"] = exact
    with app.app_context():
        persistir_execucao(**source)
        db.session.remove()
        assert all(row.quantidade_exata == exact for row in db.session.scalars(select(PrevisaoDemanda)))
        assert carregar_ativa()["result"] == source["result"]


def test_idempotencia_preserva_ids_e_retry_antigo_nao_desfaz_nova_publicacao(app):
    first, second = bundle(), bundle(2)
    with app.app_context():
        persistir_execucao(**first)
        ids = db.session.scalars(select(PrevisaoDemanda.id).order_by(PrevisaoDemanda.id)).all()
        persistir_execucao(**first)
        assert db.session.scalars(select(PrevisaoDemanda.id).order_by(PrevisaoDemanda.id)).all() == ids
        persistir_execucao(**second)
        persistir_execucao(**first)
        assert carregar_ativa()["run_id"] == second["result"]["run_id"]
        assert len(db.session.scalars(select(PrevisaoExecucao)).all()) == 2
        assert all(not item.ativa for item in db.session.scalars(select(PrevisaoExecucao).where(
            PrevisaoExecucao.id == first["result"]["run_id"])))


@pytest.mark.parametrize("change", ["hash", "path", "content", "metrics"])
def test_conflito_run_rejeita_sem_modificar_publicacao(app, change):
    source = bundle()
    with app.app_context():
        persistir_execucao(**source)
        changed = deepcopy(source)
        if change == "hash":
            changed["manifest_sha256"] = "b" * 64
        elif change == "path":
            # Não aceita nem outra pasta/manifesto com o mesmo identificador.
            changed["manifest_path"] = changed["manifest_path"].replace("manifest.json", "outra.json")
        elif change == "metrics":
            changed["report"]["evaluations"]["00101"]["selection_reason"] = "Outro motivo fictício."
        else:
            changed["result"]["generated_at"] = "2025-01-31T13:00:00+00:00"
            changed["report"]["generated_at"] = changed["result"]["generated_at"]
        with pytest.raises(RepositoryError) as error:
            persistir_execucao(**changed)
        assert error.value.code == ("INVALID_FORECAST" if change == "path" else "RUN_CONFLICT")
        assert carregar_ativa()["result"] == source["result"]
        assert len(db.session.scalars(select(PrevisaoExecucao)).all()) == 1


def test_falha_na_ativacao_reverte_linhas_e_preserva_ativa_anterior(app):
    first, second = bundle(), bundle(2)
    with app.app_context():
        persistir_execucao(**first)
        calls = []

        def fail_activation(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE previsoes_execucoes"):
                calls.append(statement)
                if len(calls) == 2:
                    raise RuntimeError("CONTEUDO_FICTICIO_SIGILOSO")

        event.listen(db.engine, "before_cursor_execute", fail_activation)
        try:
            with pytest.raises(RepositoryError) as error:
                persistir_execucao(**second)
        finally:
            event.remove(db.engine, "before_cursor_execute", fail_activation)
        assert len(calls) == 2
        assert error.value.code == "STORAGE_ERROR" and "SIGILOSO" not in str(error.value)
        assert carregar_ativa()["run_id"] == first["result"]["run_id"]
        assert db.session.get(PrevisaoExecucao, second["result"]["run_id"]) is None
        assert not db.session.scalars(select(PrevisaoDemanda).where(
            PrevisaoDemanda.execucao_id == second["result"]["run_id"])).all()


@pytest.mark.parametrize("same", [True, False])
def test_publicacoes_concorrentes_sao_serializadas_e_so_uma_fica_ativa(app, same):
    barrier = threading.Barrier(2)

    def publish(number):
        with app.app_context():
            barrier.wait(timeout=10)
            return persistir_execucao(**bundle(number))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, [1, 1 if same else 2]))
    with app.app_context():
        assert len(results) == 2
        assert len(db.session.scalars(select(PrevisaoExecucao)).all()) == (1 if same else 2)
        assert len(db.session.scalars(select(PrevisaoExecucao).where(PrevisaoExecucao.ativa.is_(True))).all()) == 1
        assert carregar_ativa()["run_id"] in {item["run_id"] for item in results}


def test_indice_banco_impede_duas_execucoes_ativas(app):
    with app.app_context():
        persistir_execucao(**bundle())
        persistir_execucao(**bundle(2))
        db.session.get(PrevisaoExecucao, bundle()["result"]["run_id"]).ativa = True
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert carregar_ativa()["run_id"] == bundle(2)["result"]["run_id"]


@pytest.mark.parametrize("changes", [
    {"quantidade_exata": "-1"}, {"quantidade_exata": "abc"}, {"quantidade_exata": ".1"},
    {"quantidade_exata": "1."}, {"quantidade_exata": "1.2.3"}, {"quantidade_exata": "0.0000000001"},
    {"quantidade_exata": "10000000000000000"}, {"codigo_produto": ""},
    {"cobertura_inicio": date(2025, 1, 1)}, {"cobertura_fim": date(2025, 3, 1)},
    {"codigo_produto": None}, {"quantidade_exata": None}, {"execucao_id": None},
    {"metricas_json": {"mae": 1}}, {"quantidade_prevista": -1},
])
def test_checks_sql_impedem_novas_linhas_incompletas_ou_invalidas(app, changes):
    with app.app_context():
        persistir_execucao(**bundle())
        row = db.session.scalars(select(PrevisaoDemanda)).first()
        for field, value in changes.items():
            setattr(row, field, value)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_unicidade_periodo_execucao_codigo_e_frequencia(app):
    with app.app_context():
        persistir_execucao(**bundle())
        row = db.session.scalars(select(PrevisaoDemanda)).first()
        copy = {column.name: getattr(row, column.name) for column in PrevisaoDemanda.__table__.columns if column.name != "id"}
        db.session.add(PrevisaoDemanda(**copy))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("field,value", [
    ("run_id", "invalido"), ("version", "v2"), ("horizon_days", True), ("horizon_days", 367),
    ("cutoff", "2025-02-30"), ("generated_at", "2025-01-31T12:00:00"),
])
def test_validacao_rejeita_metadados_invalidos_sem_persistir(app, field, value):
    source = bundle()
    source["result"][field] = value
    source["report"][field] = value
    with app.app_context(), pytest.raises(RepositoryError) as error:
        persistir_execucao(**source)
    assert error.value.code == "INVALID_FORECAST"
    with app.app_context():
        assert carregar_ativa() is None


@pytest.mark.parametrize("mutation", [
    "sum", "overall", "date", "coverage", "partial", "scale", "noncanonical", "nan",
    "model", "evaluation", "duplicate", "unknown_field", "negative", "amount_limit",
])
def test_validacao_rejeita_previsoes_incoerentes(mutation):
    source = bundle()
    result, report = source["result"], source["report"]
    view = result["forecast"]["products"]["00101"]
    daily = view["diaria"][0]
    if mutation == "sum":
        view["semanal"][0]["quantidade_prevista"] = "999"
    elif mutation == "overall":
        result["forecast"]["overall"]["total_previsto"] = "999"
    elif mutation == "date":
        daily["periodo_inicio"] = "2025-01-30"
    elif mutation == "coverage":
        view["mensal"][0]["cobertura_fim"] = "2025-01-30"
    elif mutation == "partial":
        daily["parcial"] = True
    elif mutation == "scale":
        daily["quantidade_prevista"] = "0.0000000001"
    elif mutation == "noncanonical":
        daily["quantidade_prevista"] = "0.10"
    elif mutation == "nan":
        report["evaluations"]["00101"]["model_parameters"] = {"x": float("nan")}
    elif mutation == "model":
        result["models"]["00101"] = "modelo_inventado"
    elif mutation == "evaluation":
        report["evaluations"]["00101"]["selected_model"] = "random_forest"
    elif mutation == "duplicate":
        view["diaria"].append(deepcopy(daily))
    elif mutation == "unknown_field":
        daily["cliente"] = "Cliente Fictício"
    elif mutation == "negative":
        daily["quantidade_prevista"] = "-1"
    elif mutation == "amount_limit":
        daily["quantidade_prevista"] = "10000000000000000"
    with pytest.raises(RepositoryError) as error:
        validar_execucao(result, report)
    assert error.value.code == "INVALID_FORECAST"


def test_leitura_detecta_snapshot_corrompido_com_erro_seguro(app):
    with app.app_context():
        persistir_execucao(**bundle())
        execution = db.session.get(PrevisaoExecucao, bundle()["result"]["run_id"])
        execution.resultado_json = {"conteudo_ficticio": "NAO_EXIBIR"}
        db.session.commit()
        with pytest.raises(RepositoryError) as error:
            carregar_ativa()
        assert error.value.code == "STORAGE_ERROR" and "NAO_EXIBIR" not in str(error.value)


def test_mutacao_retorno_nao_altera_snapshot_persistido(app):
    with app.app_context():
        source = bundle()
        persistir_execucao(**source)
        loaded = carregar_ativa()
        loaded["result"]["models"].clear()
        loaded["report"]["evaluations"].clear()
        assert carregar_ativa()["result"] == source["result"]


def test_execucao_real_do_motor_com_dados_ficticios_e_avaliacao_temporal_e_aceita(app):
    from app.forecasting.calendar import ManausCalendar
    from app.forecasting.evaluation import evaluate_product
    from app.forecasting.service import _comparison

    source = bundle(codes=("00101",), horizon=3)
    cutoff = date.fromisoformat(source["result"]["cutoff"])
    calendar = ManausCalendar([2024, 2025])
    index = pd.date_range(end=cutoff, periods=130)
    history = pd.Series([float(day.day % 5 + 1) if calendar.is_open(day.date()) else 0 for day in index], index=index)
    evaluation = evaluate_product(history, calendar, horizon=3,
        model_params={"random_forest": {"n_estimators": 2}, "hist_gradient_boosting": {"max_iter": 2}})
    source["result"]["forecast"] = aggregate_forecasts({"00101": evaluation["forecast"]})
    source["result"]["models"]["00101"] = evaluation["selected_model"]
    source["report"]["evaluations"]["00101"] = {
        key: value for key, value in evaluation.items() if key not in {"forecast", "forecaster"}}
    source["report"]["evaluations"]["00101"].update(test_comparison=_comparison(evaluation), model_parameters={})
    assert evaluation["fallback"] is False
    with app.app_context():
        persistir_execucao(**source)
        assert carregar_ativa()["result"] == source["result"]


def test_linha_legada_sem_produto_continua_proibida(app):
    with app.app_context():
        db.session.add(PrevisaoDemanda(produto_id=None, granularidade="diaria", periodo_inicio=date(2025, 1, 1),
                                      periodo_fim=date(2025, 1, 1), quantidade_prevista=Decimal("1"),
                                      modelo="legado_ficticio", versao_modelo="v1"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("previous", [False, True])
def test_cancelamento_impede_publicacao_e_preserva_execucao_anterior(app, previous):
    cancel = threading.Event()
    with app.app_context():
        if previous:
            persistir_execucao(**bundle())
        cancel.set()
        with pytest.raises(RepositoryError) as error:
            persistir_execucao(**bundle(2), cancel_event=cancel)
        assert error.value.code == "UPDATE_CANCELLED"
        loaded = carregar_ativa()
        assert (loaded["run_id"] if loaded else None) == (bundle()["result"]["run_id"] if previous else None)
        assert db.session.get(PrevisaoExecucao, bundle(2)["result"]["run_id"]) is None


def test_cancelamento_durante_transacao_reverte_insercoes_antes_de_ativar(app):
    cancel = threading.Event()
    with app.app_context():
        persistir_execucao(**bundle())

        def cancel_on_insert(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO previsoes_demanda"):
                cancel.set()

        event.listen(db.engine, "before_cursor_execute", cancel_on_insert)
        try:
            with pytest.raises(RepositoryError) as error:
                persistir_execucao(**bundle(2), cancel_event=cancel)
        finally:
            event.remove(db.engine, "before_cursor_execute", cancel_on_insert)
        assert error.value.code == "UPDATE_CANCELLED"
        assert carregar_ativa()["run_id"] == bundle()["result"]["run_id"]
        assert db.session.get(PrevisaoExecucao, bundle(2)["result"]["run_id"]) is None
