"""Execuções completas exclusivamente com dados e repositórios fictícios."""

import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess

import pytest

from app.forecasting import service
from app.forecasting.errors import ForecastError
from scripts import forecast_history as cli


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo ficticio"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("/data/\n")
    (root / "data/reports").mkdir(parents=True)
    (root / "data/processed").mkdir()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(service.OUTPUT_FIELDS)
    writer.writerow(("2025-12-31", "00110", "00005", "Grupo Fictício", "9", "1"))
    for offset in range(10):
        for code, quantity in (("00110", 2), ("00220", 3)):
            writer.writerow(((date(2026, 1, 1) + timedelta(days=offset)).isoformat(), code,
                             "00005", "Grupo Fictício", quantity, 1))
    content = output.getvalue()
    for name in ("canonical.csv", "reviewed.csv"):
        (root / "data/processed" / name).write_text(content, encoding="utf-8")
    preparation = {"status": "aprovado", "versao_pipeline": "2", "erros": {},
                   "politica_data": "dia_fonte", "colunas_dataset": list(service.OUTPUT_FIELDS),
                   "dataset": "data/processed/canonical.csv", "sha256_dataset": sha256(content.encode()).hexdigest()}
    prep = root / "data/reports/preparacao-ficticia.json"
    prep.write_text(json.dumps(preparation))
    review = {"status": "revisado_com_recorte", "politica_data": "dia_fonte",
              "colunas": list(service.OUTPUT_FIELDS), "contexto": {"exportacao_completa": True},
              "data_referencia": "2026-01-10", "relatorio_preparacao": prep.relative_to(root).as_posix(),
              "dataset_canonico": preparation["dataset"], "sha256_canonico": preparation["sha256_dataset"],
              "dataset_revisado": "data/processed/reviewed.csv", "sha256_revisado": preparation["sha256_dataset"]}
    path = root / "data/reports/revisao-ficticia.json"
    path.write_text(json.dumps(review))
    return root, path


def execute(project, **kwargs):
    root, path = project
    return service.run_forecast(root, path, cutoff="2026-01-10", start="2026-01-01", horizon=10, **kwargs)


def test_execucao_consulta_e_coerencia_com_origens_intactas(project):
    root, review = project
    source_paths = [review, root / "data/processed/canonical.csv", root / "data/processed/reviewed.csv"]
    before = {path: path.read_bytes() for path in source_paths}
    result = execute(project)
    manifest = json.loads(result["manifest"].read_text())
    report = json.loads(result["report"].read_text())
    assert manifest["status"] == "concluido"
    assert report["start"] == "2026-01-01"
    assert report["dataset"]["excluded_before_start"] == 1
    assert report["dataset"]["excluded_rows"]["closed"] > 0
    assert report["calendar"]["rules"]["full_holidays"] == "closed"
    assert report["calendar"]["sources"]
    assert set(manifest["models"]) == {"00110", "00220"}
    for relative, digest in manifest["files_sha256"].items():
        path = root / relative
        assert path.is_relative_to(root / "data")
        assert path.stat().st_mode & 0o777 == 0o600
        assert sha256(path.read_bytes()).hexdigest() == digest
    for value in report["evaluations"].values():
        assert value["fallback"] is True
        assert value["test"] is None
        assert value["test_comparison"]["status"] == "nao_avaliado"
    whole = service.consultar_previsao(root, result["manifest"])
    products = [service.consultar_previsao(root, result["manifest"], codigo_produto=code) for code in ("00110", "00220")]
    assert Decimal(whole["total_previsto"]) == sum(Decimal(product["total_previsto"]) for product in products)
    for index, period in enumerate(whole["periodos"]):
        assert Decimal(period["quantidade_prevista"]) == sum(Decimal(product["periodos"][index]["quantidade_prevista"]) for product in products)
    for frequency in ("diaria", "semanal", "mensal"):
        response = service.consultar_previsao(root, result["manifest"], granularidade=frequency)
        assert sum(Decimal(period["quantidade_prevista"]) for period in response["periodos"]) == Decimal(whole["total_previsto"])
    assert {path: path.read_bytes() for path in source_paths} == before
    assert not (root / "data/artifacts/sorvetes.db").exists()


def test_pipeline_longo_avalia_todos_os_candidatos_e_publica_metrica_de_teste(project):
    root, path = project
    first, cutoff = date(2025, 6, 1), date(2026, 1, 10)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(service.OUTPUT_FIELDS)
    for offset in range((cutoff - first).days + 1):
        day = first + timedelta(days=offset)
        writer.writerow((day.isoformat(), "00110", "00005", "Grupo Fictício", (offset % 11) + 1, 1))
    content = output.getvalue()
    digest = sha256(content.encode()).hexdigest()
    for name in ("canonical.csv", "reviewed.csv"):
        (root / "data/processed" / name).write_text(content, encoding="utf-8")
    prep_path = root / "data/reports/preparacao-ficticia.json"
    prep = json.loads(prep_path.read_text())
    prep["sha256_dataset"] = digest
    prep_path.write_text(json.dumps(prep))
    review = json.loads(path.read_text())
    review.update(sha256_canonico=digest, sha256_revisado=digest)
    path.write_text(json.dumps(review))
    result = service.run_forecast(root, path, cutoff=cutoff, start=first, horizon=7,
        model_params={"random_forest": {"n_estimators": 3}, "hist_gradient_boosting": {"max_iter": 3}})
    evaluation = json.loads(result["report"].read_text())["evaluations"]["00110"]
    assert not evaluation["fallback"]
    assert all(candidate["available"] and candidate["metrics"] is not None
               for candidate in evaluation["validation"]["candidates"].values())
    assert evaluation["validation"]["selection_end"] < evaluation["test"]["start"]
    assert evaluation["test_comparison"]["status"] == "avaliado_no_teste_final"
    assert {"naive", "seasonal_naive", evaluation["selected_model"]} == set(evaluation["test"]["models"])


@pytest.mark.parametrize("field,value", [("status", "pendente"), ("politica_data", "America/Manaus"),
                                       ("colunas", ["cliNome"]), ("contexto", {"exportacao_completa": False})])
def test_revisao_incompativel_bloqueia_antes_de_artefatos(project, field, value):
    root, path = project
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ForecastError):
        execute(project)
    assert not (root / "data/artifacts").exists()


@pytest.mark.parametrize("name", ["canonical.csv", "reviewed.csv"])
def test_hashes_da_cadeia_de_revisao_sao_verificados(project, name):
    root, _ = project
    (root / "data/processed" / name).write_text("DADO_FICTICIO_SIGILOSO")
    with pytest.raises(ForecastError) as caught:
        execute(project)
    assert "DADO_FICTICIO_SIGILOSO" not in str(caught.value)
    assert not (root / "data/artifacts").exists()


@pytest.mark.parametrize("kwargs", [{"horizon": 0}, {"horizon": 367}, {"folds": 0},
                                   {"start": "2026-01-11"}, {"codes": ["99999"]}])
def test_parametros_invalidos_sem_publicacao(project, kwargs):
    root, path = project
    args = {"cutoff": "2026-01-10", "start": "2026-01-01", "horizon": 10, **kwargs}
    with pytest.raises(ForecastError):
        service.run_forecast(root, path, **args)
    assert not (root / "data/artifacts").exists()


def test_corte_nao_pode_ultrapassar_a_revisao(project):
    root, path = project
    with pytest.raises(ForecastError, match="referência"):
        service.run_forecast(root, path, cutoff="2026-01-11")


def test_calendario_sem_cobertura_falha_sem_publicacao(project):
    root, path = project
    data = json.loads(path.read_text())
    data["data_referencia"] = "2027-12-31"
    path.write_text(json.dumps(data))
    with pytest.raises(ForecastError, match="2021 a 2027"):
        service.run_forecast(root, path, cutoff="2027-12-31", horizon=1)
    assert not (root / "data/artifacts").exists()


def test_excecao_operacional_privada_aplicada_ao_calendario(project):
    root, _ = project
    path = root / "data/artifacts/calendario-ficticio.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"overrides": [{"date": "2026-01-02", "is_open": False}]}))
    result = execute(project, calendar_overrides=path, codes=["00110"])
    report = json.loads(result["report"].read_text())
    assert list(report["evaluations"]) == ["00110"]
    assert report["calendar"]["overrides"] == [{"date": "2026-01-02", "is_open": False}]
    assert report["dataset"]["products"]["00110"]["first_observation"] == "2026-01-05"


def test_falha_de_modelo_nao_publica_manifesto_ou_expoe_excecao(project, monkeypatch):
    root, _ = project
    def fail(*args, **kwargs):
        raise RuntimeError("DADO_FICTICIO_SIGILOSO")
    monkeypatch.setattr(service, "evaluate_product", fail)
    with pytest.raises(ForecastError) as caught:
        execute(project)
    assert "DADO_FICTICIO_SIGILOSO" not in str(caught.value)
    assert not list((root / "data/artifacts").rglob("manifest.json"))


def test_adulteracao_de_previsao_impede_consulta(project):
    root, _ = project
    result = execute(project)
    result["forecast"].write_text("DADO_FICTICIO_SIGILOSO")
    with pytest.raises(ForecastError, match="alterada"):
        service.consultar_previsao(root, result["manifest"])


def test_consulta_codigo_e_granularidade_invalidos(project):
    root, _ = project
    result = execute(project)
    with pytest.raises(ForecastError):
        service.consultar_previsao(root, result["manifest"], codigo_produto="99999")
    with pytest.raises(ForecastError):
        service.consultar_previsao(root, result["manifest"], granularidade="anual")


def test_protecao_git_recusa_leitura_de_dados_nao_ignorados(project):
    root, _ = project
    (root / ".gitignore").write_text("")
    with pytest.raises(ForecastError):
        execute(project)


def test_cli_treino_e_consulta_nunca_imprimem_codigos_ou_quantidades(project, monkeypatch, capsys):
    root, review = project
    monkeypatch.setattr(cli, "__file__", str(root / "scripts/forecast_history.py"))
    assert cli.main(["train", "--review", str(review), "--cutoff", "2026-01-10", "--start", "2026-01-01", "--horizon", "10"]) == 0
    output = capsys.readouterr()
    assert "Manifesto privado:" in output.out
    assert "00110" not in output.out and "Grupo Fictício" not in output.out
    manifest = next((root / "data/artifacts").rglob("manifest.json"))
    assert cli.main(["query", "--manifest", str(manifest), "--code", "00110", "--granularity", "mensal"]) == 0
    output = capsys.readouterr()
    assert "Consulta privada:" in output.out and "00110" not in output.out


def test_cli_falha_controlada_sem_traceback(project, monkeypatch, capsys):
    root, review = project
    monkeypatch.setattr(cli, "__file__", str(root / "scripts/forecast_history.py"))
    assert cli.main(["train", "--review", str(review), "--cutoff", "2030-01-01"]) == 2
    assert "Traceback" not in capsys.readouterr().err
