"""Persistência atômica de previsões prontas; nunca treina ou lê modelos pickle."""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
import math
from pathlib import PurePosixPath
import re

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.database import db
from app.models.previsao_demanda import PrevisaoDemanda
from app.models.previsao_execucao import PrevisaoExecucao
from app.models.produto import Produto


MODELS = {"naive", "seasonal_naive", "random_forest", "hist_gradient_boosting"}
FREQUENCIES = ("diaria", "semanal", "mensal")
METADATA = ("version", "run_id", "generated_at", "cutoff", "horizon_days")
PERIOD_FIELDS = {"periodo_inicio", "periodo_fim", "cobertura_inicio", "cobertura_fim",
                 "parcial", "quantidade_prevista"}


class RepositoryError(ValueError):
    """Códigos estáveis e mensagens fixas, sem dados nem exceções do driver."""

    def __init__(self, code):
        self.code = code
        messages = {
            "INVALID_FORECAST": "A execução de previsão possui estrutura inválida.",
            "RUN_CONFLICT": "A execução já foi publicada com outro conteúdo.",
            "STORAGE_ERROR": "Não foi possível acessar a publicação de previsão local.",
            "UPDATE_CANCELLED": "A atualização da previsão foi cancelada antes da publicação.",
        }
        super().__init__(messages[code])


def _require(condition):
    if not condition:
        raise RepositoryError("INVALID_FORECAST")


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _day(value):
    _require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None)
    return date.fromisoformat(value)


def _code(value):
    _require(type(value) is str and 1 <= len(value) <= 64 and value == value.strip()
             and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _amount(value, *, stored=False):
    _require(type(value) is str and len(value) <= 400 and re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.[0-9]{0,8}[1-9])?", value) is not None)
    amount = Decimal(value)
    if stored:
        _require(amount < Decimal("1e16"))
    return amount


def _period(day, frequency):
    if frequency == "diaria":
        return day, day
    if frequency == "semanal":
        first = day - timedelta(days=day.weekday())
        return first, first + timedelta(days=6)
    return day.replace(day=1), day.replace(day=monthrange(day.year, day.month)[1])


def _validate_view(view, days, quantities, *, stored=False):
    _keys(view, (*FREQUENCIES, "total_previsto"))
    _require(_amount(view["total_previsto"]) == sum(quantities, Decimal(0)))
    for frequency in FREQUENCIES:
        expected = []
        for day, quantity in zip(days, quantities):
            first, last = _period(day, frequency)
            if not expected or expected[-1][0] != first:
                expected.append([first, last, day, day, Decimal(0)])
            expected[-1][3] = day
            expected[-1][4] += quantity
        rows = view[frequency]
        _require(type(rows) is list and len(rows) == len(expected))
        for row, (first, last, coverage_start, coverage_end, quantity) in zip(rows, expected):
            _keys(row, PERIOD_FIELDS)
            _require((_day(row["periodo_inicio"]), _day(row["periodo_fim"]),
                      _day(row["cobertura_inicio"]), _day(row["cobertura_fim"])) ==
                     (first, last, coverage_start, coverage_end))
            _require(type(row["parcial"]) is bool and row["parcial"] ==
                     (first != coverage_start or last != coverage_end))
            _require(_amount(row["quantidade_prevista"], stored=stored) == quantity)


def _metric(value):
    _keys(value, ("mae", "rmse", "wape", "smape"))
    for key, item in value.items():
        _require((key == "wape" and item is None) or
                 (type(item) in (int, float) and math.isfinite(item) and item >= 0))


def _text(value):
    _require(type(value) is str and len(value) <= 4000)


def _json_tree(value, depth=0):
    """Aceita apenas JSON finito e limitado, inclusive parâmetros sklearn."""
    _require(depth <= 20)
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        _require(math.isfinite(value))
    elif type(value) is str:
        _text(value)
    elif type(value) in (dict, list):
        _require(len(value) <= 10000)
        if type(value) is dict:
            for key, item in value.items():
                _require(type(key) is str and len(key) <= 128)
                _json_tree(item, depth + 1)
        else:
            for item in value:
                _json_tree(item, depth + 1)
    else:
        _require(False)


def _evaluation(value, selected, cutoff):
    _keys(value, ("selected_model", "selection_reason", "validation", "test", "fallback",
                  "test_comparison", "model_parameters"))
    _require(value["selected_model"] == selected and type(value["fallback"]) is bool)
    _text(value["selection_reason"])
    _require(type(value["model_parameters"]) is dict)
    _json_tree(value["model_parameters"])
    validation = value["validation"]
    _keys(validation, ("metric", "metric_scope", "candidates", "selection_end", "test_start"))
    _require(validation["metric"] == "mae" and validation["metric_scope"] == "open_days")
    _keys(validation["candidates"], MODELS)
    for candidate in validation["candidates"].values():
        _keys(candidate, ("metrics", "folds", "available", "reason"))
        _require(type(candidate["available"]) is bool and type(candidate["folds"]) is list
                 and len(candidate["folds"]) <= 10)
        if candidate["reason"] is not None:
            _text(candidate["reason"])
        if candidate["metrics"] is not None:
            _metric(candidate["metrics"])
        _require(not candidate["available"] or candidate["metrics"] is not None)
        for fold in candidate["folds"]:
            _keys(fold, ("train_start", "train_end", "validation_start", "validation_end", "n_open_days", "metrics"))
            _require(_day(fold["train_start"]) <= _day(fold["train_end"]) <
                     _day(fold["validation_start"]) <= _day(fold["validation_end"]) <= cutoff)
            _require(type(fold["n_open_days"]) is int and 1 <= fold["n_open_days"] <= 366)
            _metric(fold["metrics"])
    comparison, test = value["test_comparison"], value["test"]
    if value["fallback"]:
        _require(selected in {"naive", "seasonal_naive"} and test is None)
        _require(all(not candidate["available"] and candidate["metrics"] is None
                     and not candidate["folds"] for candidate in validation["candidates"].values()))
        for key in ("selection_end", "test_start"):
            if validation[key] is not None:
                _require(_day(validation[key]) <= cutoff)
        _keys(comparison, ("status", "reason"))
        _require(comparison["status"] == "nao_avaliado")
        _text(comparison["reason"])
        return
    _require(validation["candidates"][selected]["available"])
    _keys(test, ("start", "end", "n_open_days", "models"))
    _require(_day(validation["selection_end"]) < _day(validation["test_start"]) ==
             _day(test["start"]) <= _day(test["end"]) <= cutoff)
    _require(type(test["n_open_days"]) is int and 1 <= test["n_open_days"] <= 366)
    _keys(test["models"], {selected, "naive", "seasonal_naive"})
    for metric in test["models"].values():
        _metric(metric)
    for candidate in validation["candidates"].values():
        if candidate["available"]:
            _require(bool(candidate["folds"]))
        for fold in candidate["folds"]:
            _require(_day(fold["validation_end"]) <= _day(validation["selection_end"]))
    _keys(comparison, ("status", "selected_mae", "baseline_mae", "strictly_better_than_both_baselines", "note"))
    _require(comparison["status"] == "avaliado_no_teste_final")
    _keys(comparison["baseline_mae"], ("naive", "seasonal_naive"))
    _require(comparison["selected_mae"] == test["models"][selected]["mae"])
    for name in ("naive", "seasonal_naive"):
        _require(comparison["baseline_mae"][name] == test["models"][name]["mae"])
    _require(type(comparison["strictly_better_than_both_baselines"]) is bool and
             comparison["strictly_better_than_both_baselines"] ==
             (comparison["selected_mae"] < min(comparison["baseline_mae"].values())))
    _text(comparison["note"])


def _validated(result, report):
    try:
        _keys(result, (*METADATA, "forecast", "models"))
        _require(result["version"] == "forecast-v1")
        _require(type(result["run_id"]) is str and re.fullmatch(r"previsao-[0-9a-f]{32}", result["run_id"]) is not None)
        cutoff = _day(result["cutoff"])
        _require(type(result["generated_at"]) is str and len(result["generated_at"]) <= 40)
        generated = datetime.fromisoformat(result["generated_at"])
        _require(generated.tzinfo is not None and generated.utcoffset() == timedelta(0))
        _require(type(result["horizon_days"]) is int and 1 <= result["horizon_days"] <= 366)
        days = [cutoff + timedelta(days=step) for step in range(1, result["horizon_days"] + 1)]
        _keys(result["forecast"], ("products", "overall"))
        products = result["forecast"]["products"]
        _require(type(products) is dict and 1 <= len(products) <= 10000)
        _keys(result["models"], products)
        _require(type(report) is dict and all(report.get(key) == result[key] for key in METADATA))
        _keys(report.get("evaluations"), products)
        with localcontext() as context:
            context.prec = 450
            overall = [Decimal(0)] * len(days)
            for code, view in products.items():
                _code(code)
                _require(result["models"][code] in MODELS)
                _require(type(view) is dict and type(view.get("diaria")) is list and len(view["diaria"]) == len(days))
                values = [_amount(row["quantidade_prevista"], stored=True) for row in view["diaria"]]
                _validate_view(view, days, values, stored=True)
                overall = [left + right for left, right in zip(overall, values)]
                _evaluation(report["evaluations"][code], result["models"][code], cutoff)
            _validate_view(result["forecast"]["overall"], days, overall)
        # Só os metadados conhecidos e métricas são armazenados como relatório.
        metadata = {key: result[key] for key in METADATA}
        copied_result, copied_evaluations = json.loads(json.dumps(
            [result, report["evaluations"]], allow_nan=False))
        return copied_result, copied_evaluations, metadata, generated.astimezone(timezone.utc).replace(tzinfo=None), cutoff
    except RepositoryError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError):
        raise RepositoryError("INVALID_FORECAST") from None


def validar_execucao(result, report):
    """Valida o contrato F6 e a coerência das somas; retorna None e não faz IO."""
    _validated(result, report)


def _manifest_reference(path, fingerprint, run_id):
    _require(type(path) is str and len(path) <= 255 and "\\" not in path)
    parsed = PurePosixPath(path)
    _require(not parsed.is_absolute() and parsed.as_posix() == path and parsed.parts ==
             ("data", "artifacts", run_id, "manifest.json"))
    _require(type(fingerprint) is str and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None)


def _not_cancelled(cancel_event):
    if cancel_event is not None:
        _require(callable(getattr(cancel_event, "is_set", None)))
        if cancel_event.is_set():
            raise RepositoryError("UPDATE_CANCELLED")


def persistir_execucao(*, manifest_path, manifest_sha256, result, report, cancel_event=None):
    """Publica uma execução integral ou preserva a anterior, com sessão própria.

    Repetir run+hash+conteúdo é no-op e não reativa uma execução já substituída.
    Produto.codigo_externo é o único mapeamento aceito para o catálogo; ausência
    de associação conserva produto_id NULL e nunca elimina códigos históricos.
    """
    result, evaluations, metadata, generated, cutoff = _validated(result, report)
    _manifest_reference(manifest_path, manifest_sha256, result["run_id"])
    _not_cancelled(cancel_event)
    try:
        if db.engine.dialect.name != "sqlite":
            raise RepositoryError("STORAGE_ERROR")
        with Session(db.engine, expire_on_commit=False) as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                _not_cancelled(cancel_event)
                existing = session.get(PrevisaoExecucao, result["run_id"])
                if existing is not None:
                    if (existing.manifest_sha256 != manifest_sha256 or existing.manifest_path != manifest_path
                            or existing.resultado_json != result or existing.metricas_json != evaluations
                            or existing.metadados_json != metadata):
                        raise RepositoryError("RUN_CONFLICT")
                    session.commit()
                    return {"run_id": result["run_id"], "status": "concluido"}
                execution = PrevisaoExecucao(
                    id=result["run_id"], manifest_path=manifest_path, manifest_sha256=manifest_sha256,
                    gerada_em=generated, data_corte=cutoff, horizonte_dias=result["horizon_days"],
                    versao_motor=result["version"], metricas_json=evaluations,
                    resultado_json=result, metadados_json=metadata, ativa=False,
                )
                session.add(execution)
                session.flush()
                catalog = dict(session.execute(select(Produto.codigo_externo, Produto.id)
                                                .where(Produto.codigo_externo.is_not(None))).all())
                for code, view in result["forecast"]["products"].items():
                    for frequency in FREQUENCIES:
                        for period in view[frequency]:
                            session.add(PrevisaoDemanda(
                                produto_id=catalog.get(code), codigo_produto=code, execucao_id=execution.id,
                                granularidade=frequency, periodo_inicio=_day(period["periodo_inicio"]),
                                periodo_fim=_day(period["periodo_fim"]),
                                cobertura_inicio=_day(period["cobertura_inicio"]), cobertura_fim=_day(period["cobertura_fim"]),
                                quantidade_exata=period["quantidade_prevista"],
                                quantidade_prevista=Decimal(period["quantidade_prevista"]),
                                modelo=result["models"][code], versao_modelo=result["version"], gerada_em=generated,
                            ))
                # Primeiro confirma todas as linhas; a troca da publicação é última.
                session.flush()
                _not_cancelled(cancel_event)
                session.execute(update(PrevisaoExecucao).where(PrevisaoExecucao.ativa.is_(True)).values(ativa=False))
                execution.ativa = True
                _not_cancelled(cancel_event)
                session.commit()
            except BaseException:
                session.rollback()
                raise
        db.session.expire_all()
        return {"run_id": result["run_id"], "status": "concluido"}
    except RepositoryError:
        raise
    except Exception:
        raise RepositoryError("STORAGE_ERROR") from None


def carregar_ativa():
    """Lê o snapshot exato e métricas compartilhadas da única execução ativa."""
    try:
        with Session(db.engine) as session:
            execution = session.scalars(select(PrevisaoExecucao).where(PrevisaoExecucao.ativa.is_(True))).one_or_none()
            if execution is None:
                return None
            _keys(execution.metadados_json, METADATA)
            report = {**execution.metadados_json, "evaluations": execution.metricas_json}
            result, _, metadata, generated, cutoff = _validated(execution.resultado_json, report)
            _manifest_reference(execution.manifest_path, execution.manifest_sha256, execution.id)
            _require((execution.id, execution.versao_motor, execution.data_corte, execution.gerada_em,
                      execution.horizonte_dias) == (metadata["run_id"], metadata["version"], cutoff,
                                                    generated, metadata["horizon_days"]))
            return {"run_id": execution.id, "manifest_path": execution.manifest_path,
                    "manifest_sha256": execution.manifest_sha256, "result": result,
                    "report": json.loads(json.dumps(report, allow_nan=False))}
    except Exception:
        raise RepositoryError("STORAGE_ERROR") from None
