"""Evidências do snapshot de previsão, sem treinar, publicar ou abrir modelos.

O retorno contém informações históricas restritas. O chamador deve gravá-lo
exclusivamente em ``data/reports/`` e nunca imprimir seus registros em logs.
"""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import platform
import re
from types import SimpleNamespace

from app.services.previsao_service import PrevisaoError, PrevisaoService


SCHEMA_VERSION = "forecast-evidence-v1"
METRICS = ("mae", "rmse", "wape", "smape")
BASELINES = ("naive", "seasonal_naive")


def _require(condition):
    if not condition:
        raise ValueError


def _day(value):
    _require(type(value) is str)
    parsed = date.fromisoformat(value)
    _require(parsed.isoformat() == value)
    return parsed


def _integer(value, maximum):
    _require(type(value) is int and 1 <= value <= maximum)
    return value


def _interval(first, last):
    _require(first <= last)
    return {"start": first.isoformat(), "end": last.isoformat()}


def _comparison(selected, baseline):
    """Ganhos positivos indicam erro menor; denominador zero não vira ganho %."""
    result = {}
    for metric in METRICS:
        left = selected[metric] if selected is not None else None
        right = baseline[metric] if baseline is not None else None
        compared = left is not None and right is not None
        outcome = ("better" if left < right else "worse" if left > right else "tie") if compared else "not_comparable"
        # Decimal evita resíduos introduzidos pela subtração de floats no relato.
        with localcontext() as context:
            context.prec = 450
            gain = Decimal(str(right)) - Decimal(str(left)) if compared else None
            relative = gain / Decimal(str(right)) * 100 if compared and right != 0 else None
        result[metric] = {
            "selected": left, "baseline": right, "outcome": outcome,
            "absolute_error_reduction": float(gain) if gain is not None else None,
            "relative_error_reduction_percent": float(relative) if relative is not None else None,
            "relative_unavailable_reason": (
                "undefined_metric_or_not_evaluated" if not compared else
                "zero_baseline_error" if right == 0 else None
            ),
            "difference_unit": "percentage_points" if metric in {"wape", "smape"} else "product_quantity",
        }
    return result


def _provenance(report):
    source = report["provenance"]
    _require(type(source) is dict)
    for key in ("review_sha256", "dataset_sha256", "canonical_dataset_sha256"):
        _require(type(source.get(key)) is str and re.fullmatch(r"[a-f0-9]{64}", source[key]) is not None)
    _require(source.get("coverage_complete") is True and source.get("date_policy") == "dia_fonte")
    reference = _day(source["review_reference"])
    _require(reference >= _day(report["cutoff"]))
    paths = {}
    for key, directory in (("review", "reports"), ("preparation", "reports"), ("dataset", "processed")):
        value = source[key]
        _require(type(value) is str and "\\" not in value)
        path = Path(value)
        _require(not path.is_absolute() and path.as_posix() == value and ".." not in path.parts
                 and path.parts[:2] == ("data", directory))
        paths[key] = value
    return {
        **paths,
        **{key: source[key] for key in ("review_sha256", "dataset_sha256", "canonical_dataset_sha256",
                                      "review_reference", "coverage_complete", "date_policy")},
        "verification_scope": "Recorded at training; this collection verifies the published artifact snapshot, not a new source import.",
    }


def _product(code, evaluation, metadata, cutoff, config):
    first, last = _day(metadata["first_observation"]), _day(metadata["last_observation"])
    _require(first <= last <= cutoff and _day(metadata["series_end"]) == cutoff)
    final_fit = _interval(first, cutoff)
    validation = deepcopy(evaluation["validation"])
    test = deepcopy(evaluation["test"])
    selected = evaluation["selected_model"]
    selected_validation = validation["candidates"][selected]["metrics"]
    fallback = evaluation["fallback"]
    initial_train = (cutoff - first).days + 1 - config["test_days"] - config["validation_days"] * config["folds"]

    if test is not None:
        test_start = cutoff - timedelta(days=config["test_days"] - 1)
        train_end = test_start - timedelta(days=1)
        _require(not fallback and initial_train >= 1 and _day(test["start"]) == test_start
                 and _day(test["end"]) == cutoff and _day(validation["selection_end"]) == train_end
                 and _day(validation["test_start"]) == test_start)
        test["training_interval"] = _interval(first, train_end)
        test["training_interval_basis"] = "Full daily series before the untouched final test; not the final refit."
        for candidate in validation["candidates"].values():
            if candidate["available"]:
                _require(len(candidate["folds"]) == config["folds"])
            for index, fold in enumerate(candidate["folds"]):
                end = first + timedelta(days=initial_train + index * config["validation_days"] - 1)
                _require(_day(fold["train_start"]) == first and _day(fold["train_end"]) == end
                         and _day(fold["validation_start"]) == end + timedelta(days=1)
                         and _day(fold["validation_end"]) == end + timedelta(days=config["validation_days"]))
    else:
        _require(fallback)

    comparisons = {
        "validation": {
            baseline: _comparison(selected_validation, validation["candidates"][baseline]["metrics"])
            for baseline in BASELINES
        },
        "test": {
            baseline: _comparison(test["models"][selected] if test is not None else None,
                                  test["models"][baseline] if test is not None else None)
            for baseline in BASELINES
        },
    }
    selected_test = test["models"][selected] if test is not None else None
    return {
        "codigo_produto": code,
        "codigo_grupo": metadata.get("codigo_grupo"),
        "categoria": metadata.get("categoria"),
        "selected_model": selected,
        "status": "not_evaluated" if test is None else "evaluated_final_test",
        "fallback": fallback,
        "selection_reason": evaluation["selection_reason"],
        "final_fit": final_fit,
        "final_fit_note": "Refit used for the published future forecast, separate from validation folds and pre-test training.",
        "selected_model_parameters": deepcopy(evaluation["model_parameters"]),
        "random_state_recorded": evaluation["model_parameters"].get("random_state"),
        "validation": validation,
        "test": test,
        "comparisons": comparisons,
        "wape_undefined": {
            "selected_validation": selected_validation is not None and selected_validation["wape"] is None,
            "selected_test": selected_test is not None and selected_test["wape"] is None,
        },
        "strictly_better_than_both_test_baselines": (
            all(comparisons["test"][baseline]["mae"]["outcome"] == "better" for baseline in BASELINES)
            if test is not None else None
        ),
    }


def _summary(products):
    assessed = [product for product in products if product["test"] is not None]
    return {
        "products": len(products),
        "evaluated_final_test": len(assessed),
        "not_evaluated": len(products) - len(assessed),
        "fallback": sum(product["fallback"] for product in products),
        "selected_models": dict(sorted(Counter(product["selected_model"] for product in products).items())),
        "strict_mae_gain_against_both_baselines": sum(
            product["strictly_better_than_both_test_baselines"] is True for product in products),
        "selected_test_wape_undefined": sum(product["wape_undefined"]["selected_test"] for product in products),
        "test_mae_outcomes": {
            baseline: {outcome: sum(product["comparisons"]["test"][baseline]["mae"]["outcome"] == outcome
                                    for product in products)
                       for outcome in ("better", "tie", "worse", "not_comparable")}
            for baseline in BASELINES
        },
        "metric_averages": None,
        "statistical_significance_assessed": False,
    }


def collect_forecast_evidence(project_root: Path, manifest_path: Path) -> dict:
    """Valida e extrai evidências privadas de uma execução já concluída.

    Usa o mesmo validador de ativação do agente, com todos os hashes conferidos.
    Não cria Flask/SQLite, não altera a execução ativa e não desserializa pickle.
    Também não refaz o treino nem recalcula métricas com modelos diferentes.
    """
    try:
        root = Path(project_root).resolve()
        adapter = PrevisaoService(SimpleNamespace(config={"FORECAST_PROJECT_ROOT": root}, testing=True))
        path, digest, result, report = adapter._bundle(manifest_path, check_all=True)
        # O mesmo arquivo precisa representar o snapshot já validado acima.
        content = path.read_bytes()
        _require(sha256(content).hexdigest() == digest)
        manifest = adapter._json(content)
        config = report["evaluation_config"]
        _require(type(config) is dict and config.get("selection_metric") == "mae_open_days")
        for key, maximum in (("test_days", 366), ("validation_days", 366), ("folds", 10)):
            _integer(config[key], maximum)
        metadata = report["dataset"]["products"]
        _require(type(metadata) is dict and set(metadata) == set(result["models"]))
        cutoff = _day(result["cutoff"])
        products = [_product(code, report["evaluations"][code], metadata[code], cutoff, config)
                    for code in sorted(metadata)]

        overall = result["forecast"]["overall"]
        # validar_execucao, chamado por _bundle, já comprovou exatamente estas somas.
        return {
            "schema_version": SCHEMA_VERSION,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "run": {key: result[key] for key in ("run_id", "version", "generated_at", "cutoff", "horizon_days")},
            "integrity": {
                "manifest": path.relative_to(root).as_posix(),
                "manifest_sha256": digest,
                "all_artifact_hashes_verified": True,
                "artifact_files_sha256": deepcopy(manifest["files_sha256"]),
                "model_deserialized": False,
                "database_accessed": False,
            },
            "provenance": _provenance(report),
            "reproducibility": {
                "artifact_environment": {"python": manifest.get("python"),
                                         "libraries": deepcopy(manifest.get("versions", {})),
                                         "platform": None},
                "collection_environment": {"python": platform.python_version(), "platform": platform.platform(),
                                           "libraries": {name: version(name) for name in
                                                         ("numpy", "pandas", "scikit-learn", "threadpoolctl")}},
                "source_sha256": deepcopy(manifest.get("source_sha256", {})),
                "evaluation_config": deepcopy(config),
                "calendar": deepcopy(report["calendar"]),
                "random_state_note": "Per-product values come from recorded final estimator parameters; baselines have no random state. Unselected estimator defaults and the training OS were not persisted.",
            },
            "metric_definitions": {
                "scope": "Open business days only, within each recorded temporal split.",
                "mae_rmse_unit": "product_quantity",
                "wape_smape_unit": "percent",
                "wape_undefined": "Zero observed total with nonzero prediction error is null; zero observations and zero error yield zero.",
                "selection": "Validation MAE selects the model; the final test only measures it.",
            },
            "products": products,
            "summary": _summary(products),
            "general": {
                "forecast_sum_coherent": True,
                "daily_weekly_monthly_coherent": True,
                "total_previsto": overall["total_previsto"],
                "metrics": None,
                "metrics_unavailable_reason": "The published artifact does not retain aligned observed and predicted final-test vectors. Product errors cannot be averaged or summed into general-series metrics.",
                "quantity_note": "Sum of heterogeneous commercial quantities, not liters, mass or revenue.",
            },
            "limitations": [
                "These are point comparisons for one temporal experiment, not a statistical significance test or a universal improvement claim.",
                "A baseline selected as the winner ties itself; this is not a gain over that baseline.",
                "Fallback products have future forecasts but no validated performance claim.",
                "Validation metrics pool the open-day errors from the recorded rolling folds; the collector does not average fold metrics.",
                "The artifact environment is distinct from the environment collecting this evidence; the original training platform was not recorded.",
                "Source dataset and review hashes are provenance recorded at training; no raw historical CSV is imported during collection.",
            ],
        }
    except PrevisaoError:
        raise
    except Exception:
        raise PrevisaoError("INVALID_ARTIFACT", "Não foi possível validar as evidências da execução de previsão local.") from None
