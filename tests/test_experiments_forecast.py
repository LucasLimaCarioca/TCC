"""Evidências experimentais somente com fontes e artefatos fictícios."""

import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path

import pytest

from app.experiments.forecast_evidence import collect_forecast_evidence
from app.forecasting import service
from app.services.previsao_service import PrevisaoError
from test_forecasting_service import project, execute


@pytest.fixture
def evaluated(project):
    root, review_path = project
    first, cutoff = date(2025, 6, 2), date(2026, 1, 10)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(service.OUTPUT_FIELDS)
    for offset in range((cutoff - first).days + 1):
        day = first + timedelta(days=offset)
        for code, value in (("00110", (offset % 11) + 1), ("00220", 0)):
            writer.writerow((day.isoformat(), code, "00005", "Grupo Fictício", value, 1))
    content = output.getvalue()
    digest = sha256(content.encode()).hexdigest()
    for name in ("canonical.csv", "reviewed.csv"):
        (root / "data/processed" / name).write_text(content)
    prep_path = root / "data/reports/preparacao-ficticia.json"
    prep = json.loads(prep_path.read_text())
    prep["sha256_dataset"] = digest
    prep_path.write_text(json.dumps(prep))
    review = json.loads(review_path.read_text())
    review.update(sha256_canonico=digest, sha256_revisado=digest)
    review_path.write_text(json.dumps(review))
    paths = service.run_forecast(root, review_path, cutoff=cutoff, start=first, horizon=7,
        model_params={"random_forest": {"n_estimators": 3}, "hist_gradient_boosting": {"max_iter": 3}})
    return root, paths


def rewrite_artifact(root, paths, target, transform):
    """Reconstrói o hash de um artefato fictício para testar seu schema semântico."""
    path = paths[target]
    value = json.loads(path.read_text())
    transform(value)
    path.write_text(json.dumps(value, allow_nan=False))
    manifest_path = paths["manifest"]
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"][path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


def test_evidencias_preservam_folds_teste_e_refit_separados_sem_efeitos_colaterais(evaluated, monkeypatch, capsys):
    root, paths = evaluated
    before = {path: path.read_bytes() for path in (root / "data").rglob("*") if path.is_file()}
    report = json.loads(paths["report"].read_text())

    def forbidden(*args, **kwargs):
        pytest.fail("A coleta não deve treinar, desserializar modelos ou acessar banco.")
    monkeypatch.setattr("pickle.loads", forbidden)
    monkeypatch.setattr("pickle.load", forbidden)
    monkeypatch.setattr(service, "run_forecast", forbidden)
    monkeypatch.setattr("app.forecasting.models.Forecaster.fit", forbidden)
    monkeypatch.setattr("app.services.previsao_repository.persistir_execucao", forbidden)
    monkeypatch.setattr("app.services.previsao_repository.carregar_ativa", forbidden)
    evidence = collect_forecast_evidence(root, paths["manifest"])

    assert {path: path.read_bytes() for path in (root / "data").rglob("*") if path.is_file()} == before
    assert capsys.readouterr() == ("", "")
    assert not (root / "data/artifacts/sorvetes.db").exists()
    assert evidence["integrity"]["all_artifact_hashes_verified"] is True
    assert evidence["integrity"]["model_deserialized"] is False
    assert evidence["integrity"]["database_accessed"] is False
    assert [product["codigo_produto"] for product in evidence["products"]] == ["00110", "00220"]
    for product in evidence["products"]:
        original = report["evaluations"][product["codigo_produto"]]
        assert product["status"] == "evaluated_final_test"
        assert product["validation"] == original["validation"]
        assert product["test"]["models"] == original["test"]["models"]
        assert product["test"]["training_interval"] == {"start": "2025-06-02", "end": "2025-12-27"}
        assert product["test"]["start"] == "2025-12-28"
        assert product["test"]["end"] == "2026-01-10"
        assert product["final_fit"] == {"start": "2025-06-02", "end": "2026-01-10"}
        assert product["selected_model_parameters"] == original["model_parameters"]
        for candidate in product["validation"]["candidates"].values():
            assert len(candidate["folds"]) == 3
            for fold in candidate["folds"]:
                assert fold["train_end"] < fold["validation_start"] <= fold["validation_end"] < product["test"]["start"]
    zero = evidence["products"][1]
    assert zero["selected_model"] == "naive"
    assert zero["test"]["models"]["naive"] == {"mae": 0, "rmse": 0, "wape": 0, "smape": 0}
    assert zero["comparisons"]["test"]["naive"]["mae"]["outcome"] == "tie"
    assert zero["comparisons"]["test"]["naive"]["mae"]["relative_error_reduction_percent"] is None
    assert zero["strictly_better_than_both_test_baselines"] is False
    assert evidence["summary"]["metric_averages"] is None
    assert evidence["summary"]["statistical_significance_assessed"] is False
    assert evidence["general"]["metrics"] is None
    assert evidence["general"]["forecast_sum_coherent"] is True
    assert Decimal(evidence["general"]["total_previsto"]) >= 0
    assert evidence["reproducibility"]["artifact_environment"]["platform"] is None
    assert evidence["reproducibility"]["artifact_environment"]["libraries"]["scikit-learn"]
    assert evidence["reproducibility"]["source_sha256"]
    assert evidence["provenance"]["dataset_sha256"] == report["provenance"]["dataset_sha256"]
    assert json.loads(json.dumps(evidence, allow_nan=False)) == evidence


def test_fallback_nao_ganha_metrica_ou_comparacao_inventada(project):
    root, _ = project
    paths = execute(project)
    evidence = collect_forecast_evidence(root, paths["manifest"])
    assert evidence["summary"]["fallback"] == evidence["summary"]["not_evaluated"] == 2
    assert evidence["summary"]["evaluated_final_test"] == 0
    for product in evidence["products"]:
        assert product["fallback"] is True and product["test"] is None
        assert product["strictly_better_than_both_test_baselines"] is None
        for phase in ("validation", "test"):
            for metrics in product["comparisons"][phase].values():
                assert all(value["outcome"] == "not_comparable" for value in metrics.values())
                assert all(value["absolute_error_reduction"] is None for value in metrics.values())


def test_ganho_piora_empate_e_wape_indefinida_sao_relacionados_sem_alegacao_global(evaluated):
    root, paths = evaluated
    # Artefato inteiramente fictício: fixa três resultados distintos no teste final.
    def evaluation(value):
        item = value["evaluations"]["00110"]
        item["selected_model"] = "random_forest"
        item["test"]["models"] = {
            "random_forest": {"mae": 2, "rmse": 4, "wape": None, "smape": 5},
            "naive": {"mae": 4, "rmse": 2, "wape": 0, "smape": 5},
            "seasonal_naive": {"mae": 1, "rmse": 0, "wape": None, "smape": 10},
        }
        item["test_comparison"].update(selected_mae=2, baseline_mae={"naive": 4, "seasonal_naive": 1},
                                        strictly_better_than_both_baselines=False)
    rewrite_artifact(root, paths, "report", evaluation)
    rewrite_artifact(root, paths, "forecast", lambda value: value["models"].update({"00110": "random_forest"}))
    evidence = collect_forecast_evidence(root, paths["manifest"])
    product = evidence["products"][0]
    naive, seasonal = product["comparisons"]["test"]["naive"], product["comparisons"]["test"]["seasonal_naive"]
    assert naive["mae"]["outcome"] == "better"
    assert naive["mae"]["absolute_error_reduction"] == 2
    assert naive["mae"]["relative_error_reduction_percent"] == 50
    assert naive["rmse"]["outcome"] == "worse"
    assert naive["rmse"]["absolute_error_reduction"] == -2
    assert naive["smape"]["outcome"] == "tie"
    assert naive["wape"]["outcome"] == "not_comparable"
    assert seasonal["mae"]["outcome"] == "worse"
    assert seasonal["mae"]["relative_error_reduction_percent"] == -100
    assert seasonal["rmse"]["outcome"] == "worse"
    assert seasonal["rmse"]["relative_error_reduction_percent"] is None
    assert seasonal["rmse"]["relative_unavailable_reason"] == "zero_baseline_error"
    assert seasonal["smape"]["absolute_error_reduction"] == 5
    assert seasonal["smape"]["difference_unit"] == "percentage_points"
    assert product["wape_undefined"]["selected_test"] is True
    assert product["strictly_better_than_both_test_baselines"] is False
    assert evidence["summary"]["selected_test_wape_undefined"] == 1
    assert evidence["summary"]["test_mae_outcomes"]["naive"]["better"] == 1
    assert evidence["summary"]["test_mae_outcomes"]["seasonal_naive"]["worse"] == 1


@pytest.mark.parametrize("target", ["forecast", "report", "series", "model", "manifest"])
def test_qualquer_artefato_adulterado_bloqueia_coleta_sem_vazar_conteudo(project, target):
    root, _ = project
    paths = execute(project)
    path = paths[target] if target != "model" else root / next(iter(json.loads(paths["manifest"].read_text())["models"].values()))
    path.write_bytes(b"MARCADOR_FICTICIO_PRIVADO")
    with pytest.raises(PrevisaoError) as caught:
        collect_forecast_evidence(root, paths["manifest"])
    assert caught.value.code == "INVALID_ARTIFACT"
    assert "MARCADOR_FICTICIO" not in str(caught.value)


@pytest.mark.parametrize("change", [
    lambda value: value["dataset"]["products"]["00110"].update(first_observation="2025-06-03"),
    lambda value: value["dataset"]["products"]["00110"].update(series_end="2026-01-09"),
    lambda value: value["evaluation_config"].update(test_days=13),
    lambda value: value["evaluation_config"].update(folds=True),
    lambda value: value["provenance"].update(dataset="data/processed/../../arquivo"),
])
def test_metadados_temporais_e_proveniencia_invalidos_nao_geram_evidencia(evaluated, change):
    root, paths = evaluated
    rewrite_artifact(root, paths, "report", change)
    with pytest.raises(PrevisaoError) as caught:
        collect_forecast_evidence(root, paths["manifest"])
    assert caught.value.code == "INVALID_ARTIFACT"


def test_caminho_publico_ou_fora_do_projeto_nao_e_aceito(project):
    root, _ = project
    paths = execute(project)
    public = root / "manifesto-publico.json"
    public.write_bytes(paths["manifest"].read_bytes())
    with pytest.raises(PrevisaoError):
        collect_forecast_evidence(root, public)


def test_coleta_nao_reimporta_csv_original_ou_preparado(project, monkeypatch):
    root, _ = project
    paths = execute(project)
    original = root / "data/pedido.csv"
    original.write_text("FONTE_FICTICIA_NAO_DEVE_SER_LIDA")
    blocked = {original, root / "data/processed/canonical.csv", root / "data/processed/reviewed.csv"}
    read_bytes = Path.read_bytes
    read_text = Path.read_text

    def protected_bytes(path, *args, **kwargs):
        assert path not in blocked, "A coleta usa o snapshot, sem importar novamente a fonte."
        return read_bytes(path, *args, **kwargs)

    def protected_text(path, *args, **kwargs):
        assert path not in blocked, "A coleta usa o snapshot, sem importar novamente a fonte."
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", protected_bytes)
    monkeypatch.setattr(Path, "read_text", protected_text)
    evidence = collect_forecast_evidence(root, paths["manifest"])
    assert evidence["summary"]["products"] == 2
