"""Orquestração/exportação experimental com dados exclusivamente fictícios."""

from copy import deepcopy
import csv
from hashlib import sha256
import io
import json
import stat

import pytest

from app.experiments import service
from app.experiments.errors import ExperimentError
from scripts import validate_tccii as cli
from test_experiments_forecast import evaluated
from test_forecasting_service import project, execute


def functional_result():
    return {"status": "aprovado", "data_origin": "inteiramente_ficticia",
        "production_database_used": False,
        "checks": [{"id": name, "status": "aprovado"} for name in sorted(service.REQUIRED_CHECKS)],
        "communication": {"transport": "xmpp_localhost", "sent": 4, "received": 4,
            "failure_sent": 2, "failure_received": 2,
            "counts": [{"ontology": "infra.ping", "performative": "request", "status": "sent", "count": 4}]},
        "timings": [{"name": "atendimento.tcc_i_01", "scope": "http_atendimento_caso_completo", "seconds": 0.01}]}


@pytest.fixture
def short_run(project):
    return project[0], execute(project)


def _json(result):
    return json.loads(result["report"].read_text())


def _csv(result, name):
    return list(csv.DictReader(io.StringIO((result["directory"] / name).read_text())))


def test_execucao_integral_usa_xmpp_real_isolado_e_exporta_sem_alterar_fonte(short_run, capsys):
    root, paths = short_run
    before = {path: path.read_bytes() for path in (root / "data").rglob("*") if path.is_file()}
    result = service.run_validation(root, [paths["manifest"]])
    report = _json(result)
    assert result["status"] == report["status"] == "aprovado"
    assert report["scope"] == "funcional_e_previsao"
    assert report["phase10_evidence_complete"] is False  # Curto demais: todos usam fallback.
    assert report["forecast_final_test_available"] is False
    assert {path: path.read_bytes() for path in before} == before
    assert report["functional"]["communication"]["sent"] == report["functional"]["communication"]["received"] > 0
    assert len(report["functional"]["checks"]) == 31
    assert len(_csv(result, "cenarios.csv")) == 31
    assert len(_csv(result, "tempos.csv")) == 22
    assert all(row["status"] == "aprovado" for row in _csv(result, "cenarios.csv"))
    assert not (root / "data/artifacts/sorvetes.db").exists()
    assert report["environment"]["experiment_source_sha256"]["app/experiments/service.py"]
    assert report["environment"]["libraries"]["spade"]
    assert capsys.readouterr() == ("", "")
    for name, digest in report["export_sha256"].items():
        assert sha256((result["directory"] / name).read_bytes()).hexdigest() == digest
    for path in result["directory"].iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.is_relative_to(root / "data/reports")
    assert stat.S_IMODE(result["directory"].stat().st_mode) == 0o700
    assert all(row["avaliado"] == "False" and row["mae"] == "" for row in _csv(result, "metricas.csv"))


def test_exportacao_preserva_metricas_folds_intervalos_baselines_e_zeros(evaluated, monkeypatch):
    root, paths = evaluated
    monkeypatch.setattr(service, "run_functional_experiment", lambda _: functional_result())
    result = service.run_validation(root, [paths["manifest"]])
    report = _json(result)
    assert report["phase10_evidence_complete"] is result["evidence_complete"] is True
    rows = _csv(result, "metricas.csv")
    product = report["forecasts"][0]["products"][0]
    final_test = [row for row in rows if row["codigo_produto"] == "00110" and row["etapa"] == "teste_final"]
    assert {row["modelo"] for row in final_test} == set(product["test"]["models"])
    for row in final_test:
        assert row["treino_inicio"] == "2025-06-02" and row["treino_fim"] == "2025-12-27"
        assert row["avaliacao_inicio"] == "2025-12-28" and row["avaliacao_fim"] == "2026-01-10"
        for metric in ("mae", "rmse", "wape", "smape"):
            expected = product["test"]["models"][row["modelo"]][metric]
            assert row[metric] == (str(expected) if expected is not None else "")
    assert {row["fold"] for row in rows if row["etapa"] == "fold_validacao"} == {"1", "2", "3"}
    zero = next(row for row in rows if row["codigo_produto"] == "00220" and row["etapa"] == "teste_final" and row["modelo"] == "naive")
    assert float(zero["mae"]) == 0 and zero["wape_definida"] == "True"
    comparisons = _csv(result, "comparacoes.csv")
    zero_comparison = next(row for row in comparisons if row["codigo_produto"] == "00220" and row["etapa"] == "test" and row["baseline"] == "naive" and row["metrica"] == "mae")
    assert zero_comparison["outcome"] == "tie"
    assert zero_comparison["relative_error_reduction_percent"] == ""
    assert float(zero_comparison["baseline_value"]) == 0
    assert report["forecasts"][0]["general"]["metrics"] is None


def test_execucao_somente_funcional_e_repeticao_criam_pacotes_novos(project, monkeypatch):
    root, _ = project
    monkeypatch.setattr(service, "run_functional_experiment", lambda _: functional_result())
    first = service.run_validation(root, functional_only=True)
    before = first["report"].read_bytes()
    second = service.run_validation(root, functional_only=True)
    assert first["directory"] != second["directory"]
    assert first["report"].read_bytes() == before
    report = _json(second)
    assert report["forecasts"] == [] and report["scope"] == "somente_funcional"
    assert report["phase10_evidence_complete"] is False
    assert _csv(second, "metricas.csv") == _csv(second, "comparacoes.csv") == []


@pytest.mark.parametrize("manifests,only", [([], False), (["arquivo"], True), (None, False),
    ("arquivo", False), ([1], False), ([], 1), (["arquivo"] * 11, False)])
def test_argumentos_ambiguos_sao_rejeitados_sem_iniciar_experimento(project, monkeypatch, manifests, only):
    root, _ = project
    monkeypatch.setattr(service, "run_functional_experiment", lambda _: pytest.fail("Não pode iniciar."))
    with pytest.raises(ExperimentError) as error:
        service.run_validation(root, manifests, functional_only=only)
    assert error.value.code == "INVALID_INPUT"


def test_mesma_execucao_previsao_nao_pode_ser_contada_duas_vezes(short_run, monkeypatch):
    root, paths = short_run
    monkeypatch.setattr(service, "run_functional_experiment", lambda _: pytest.fail("Não pode iniciar."))
    with pytest.raises(ExperimentError) as error:
        service.run_validation(root, [paths["manifest"], paths["manifest"]])
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("kind", ["public", "symlink", "not_ignored"])
def test_saida_fora_da_protecao_privada_falha_antes_do_runtime(project, monkeypatch, tmp_path, kind):
    root, _ = project
    monkeypatch.setattr(service, "run_functional_experiment", lambda _: pytest.fail("Não pode iniciar."))
    if kind == "symlink":
        (root / "data/artifacts").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "not_ignored":
        (root / ".gitignore").write_text("")
    else:
        real_private_path = service.private_path
        monkeypatch.setattr(service, "private_path", lambda *args: real_private_path(root, "public"))
    with pytest.raises(ExperimentError) as error:
        service.run_validation(root, functional_only=True)
    assert error.value.code == "INVALID_PATH"


@pytest.mark.parametrize("change", [
    lambda item: item.update(checks=[]),
    lambda item: item.update(checks=item["checks"][:-1]),
    lambda item: item["checks"][0].update(status="falhou"),
    lambda item: item["checks"].append(deepcopy(item["checks"][0])),
    lambda item: item.update(production_database_used=True),
    lambda item: item.update(communication=None),
    lambda item: item["timings"][0].update(seconds=float("nan")),
])
def test_retorno_parcial_nao_gera_estado_falso_de_aprovacao(project, monkeypatch, change):
    root, _ = project
    value = functional_result()
    change(value)
    monkeypatch.setattr(service, "run_functional_experiment", lambda _: value)
    result = service.run_validation(root, functional_only=True)
    assert _json(result)["status"] == "falhou"
    assert result["evidence_complete"] is False


def test_excecao_funcional_fica_sanitizada_e_evidencia_falha_e_preservada(project, monkeypatch):
    root, _ = project
    def fail(_):
        raise RuntimeError("SEGREDO_FICTICIO_NAO_EXIBIR")
    monkeypatch.setattr(service, "run_functional_experiment", fail)
    result = service.run_validation(root, functional_only=True)
    assert result["status"] == "falhou"
    assert "SEGREDO_FICTICIO" not in result["report"].read_text()
    assert _csv(result, "cenarios.csv")[0]["status"] == "falhou"


def test_cli_imprime_apenas_caminhos_status_e_nao_conteudo(short_run, monkeypatch, capsys):
    root, paths = short_run
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    monkeypatch.setattr(service, "run_functional_experiment", lambda _: functional_result())
    assert cli.main(["--manifest", str(paths["manifest"])]) == 0
    output = capsys.readouterr()
    assert "Relatório privado:" in output.out and "data/reports/validacao-tccii-" in output.out
    assert "não conclui a evidência integral" in output.out
    assert "00110" not in output.out and "00220" not in output.out and "mae" not in output.out
    assert output.err == ""


@pytest.mark.parametrize("args", [[], ["--functional-only", "--manifest", "arquivo"]])
def test_cli_exige_escopo_explicito(args):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2


def test_cli_falha_sem_expor_excecao_ou_artefato(project, monkeypatch, capsys):
    root, _ = project
    monkeypatch.setattr(cli, "PROJECT_ROOT", root)
    def fail(*args, **kwargs):
        raise RuntimeError("SEGREDO_FICTICIO_NAO_EXIBIR")
    monkeypatch.setattr(cli, "run_validation", fail)
    assert cli.main(["--functional-only"]) == 2
    assert "SEGREDO_FICTICIO" not in capsys.readouterr().err

