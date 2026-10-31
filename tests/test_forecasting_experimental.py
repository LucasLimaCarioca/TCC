"""Seleções e observações totalmente fictícias, independentes dos dados locais."""

from copy import deepcopy
import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import io
import json

import pytest

from app.database import db
from app.forecasting import service
from app.forecasting.experimental import context_signature, validate_experimental_context
from app.forecasting.errors import ForecastError
from app.models.previsao_demanda import PrevisaoDemanda
from app.models.previsao_execucao import PrevisaoExecucao
from app.services import catalogacao_service as catalog
from app.services import previsao_repository as repository
from scripts import forecast_history as cli
from test_forecasting_service import project, execute
from test_previsao_repository import bundle


def context(codes=("00110",)):
    value = {"versao": 1, "natureza": "experimental", "origem_historico": "real",
        "estoque": "simulado", "vendas_operacionais": "simuladas",
        "associacoes": [{"produto_id": index + 40, "codigo_produto": code,
                          "versao_revisao": "a" * 64} for index, code in enumerate(sorted(codes))]}
    value["assinatura"] = context_signature(value)
    return value


def replace_dataset(project, first, reference, cutoff, *, future=50):
    root, review_path = project
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(service.LEGACY_OUTPUT_FIELDS)
    for offset in range((reference - first).days + 1):
        day = first + timedelta(days=offset)
        for code in ("00110", "00220"):
            quantity = future if day > cutoff else offset % 11 + 1
            writer.writerow((day.isoformat(), code, "00005", "Grupo Fictício", quantity, 1))
    content = output.getvalue()
    digest = sha256(content.encode()).hexdigest()
    for name in ("canonical.csv", "reviewed.csv"):
        (root / "data/processed" / name).write_text(content, encoding="utf-8")
    path = root / "data/reports/preparacao-ficticia.json"
    preparation = json.loads(path.read_text())
    preparation["sha256_dataset"] = digest
    path.write_text(json.dumps(preparation))
    review = json.loads(review_path.read_text())
    review.update(data_referencia=reference.isoformat(), sha256_canonico=digest, sha256_revisado=digest)
    review_path.write_text(json.dumps(review))


def experimental_bundle():
    source = bundle(codes=("00101", "00102"))
    selected = context(("00101", "00102"))
    source["result"]["experimento"] = selected
    source["report"]["experimento"] = deepcopy(selected)
    source["report"]["dataset"]["products"] = {code: {} for code in ("00101", "00102")}
    return source


def test_contrato_preserva_codigos_e_devolve_copia_independente():
    selected = context(("00001", "00110"))
    validated = validate_experimental_context(selected, codes=("00110", "00001"))
    assert validated == selected and validated is not selected
    validated["associacoes"][0]["codigo_produto"] = "OUTRO-FICTICIO"
    assert selected["associacoes"][0]["codigo_produto"] == "00001"
    assert selected["associacoes"][0]["produto_id"] != 1


@pytest.mark.parametrize("change", [
    lambda value: value.update(versao=True),
    lambda value: value.update(estoque="real"),
    lambda value: value.update(vendas_operacionais="reais"),
    lambda value: value.update(associacoes=[]),
    lambda value: value.update(assinatura="b"*64),
    lambda value: value["associacoes"][0].update(produto_id=True),
    lambda value: value["associacoes"][0].update(codigo_produto=" 00110"),
    lambda value: value["associacoes"][0].update(versao_revisao="invalida"),
    lambda value: value["associacoes"].append(deepcopy(value["associacoes"][0])),
    lambda value: value.update(campo_desconhecido="ficticio"),
])
def test_contrato_rejeita_tipo_origem_duplicidade_ou_assinatura_invalida(change):
    selected = context()
    original_signature = selected["assinatura"]
    change(selected)
    if selected.get("assinatura") == original_signature:
        selected["assinatura"] = context_signature(selected)
    with pytest.raises(ValueError, match="Seleção experimental inválida"):
        validate_experimental_context(selected)


def test_contrato_exige_mesmos_codigos_e_ordenacao():
    selected = context(("00110", "00220"))
    with pytest.raises(ValueError):
        validate_experimental_context(selected, codes=("00110",))
    selected["associacoes"].reverse()
    selected["assinatura"] = context_signature(selected)
    with pytest.raises(ValueError):
        validate_experimental_context(selected)
    selected = context(("00110", "00220"))
    selected["associacoes"][1]["produto_id"] = selected["associacoes"][0]["produto_id"]
    selected["assinatura"] = context_signature(selected)
    with pytest.raises(ValueError):
        validate_experimental_context(selected)
    with pytest.raises(ValueError):
        validate_experimental_context(context(tuple(f"{index:05d}" for index in range(12))))


def test_motor_filtra_treino_avaliacao_modelos_e_total_geral_sem_alterar_fontes(project):
    root, review = project
    sources = [review, root / "data/processed/canonical.csv", root / "data/processed/reviewed.csv"]
    before = {path: path.read_bytes() for path in sources}
    selected = context()
    paths = execute(project, experimental_context=selected)
    result = json.loads(paths["forecast"].read_text())
    report = json.loads(paths["report"].read_text())
    manifest = json.loads(paths["manifest"].read_text())
    assert result["experimento"] == report["experimento"] == manifest["experimento"] == selected
    assert set(result["models"]) == set(result["forecast"]["products"]) == {"00110"}
    assert set(report["evaluations"]) == set(report["dataset"]["products"]) == {"00110"}
    assert report["dataset"]["requested_codes"] == ["00110"]
    assert report["dataset"]["excluded_not_selected"] > 0
    rows = list(csv.DictReader(io.StringIO(paths["series"].read_text())))
    assert {row["codigo_produto"] for row in rows} == {"00110"}
    assert result["forecast"]["overall"] == result["forecast"]["products"]["00110"]
    assert report["avaliacao_pos_corte"]["00110"]["status"] == "nao_avaliado"
    summary = paths["summary"].read_text()
    assert "histórico de demanda é real" in summary and "operacionais são simulados" in summary
    assert "exclusivamente os códigos históricos selecionados" in summary
    assert {path: path.read_bytes() for path in sources} == before
    consulted = service.consultar_previsao(root, paths["manifest"])
    assert consulted["experimento"] == selected
    assert Decimal(consulted["total_previsto"]) == Decimal(result["forecast"]["products"]["00110"]["total_previsto"])
    with pytest.raises(ForecastError):
        service.consultar_previsao(root, paths["manifest"], codigo_produto="00220")


def test_nova_selecao_cria_execucao_independente_e_preserva_artefatos_anteriores(project):
    first = execute(project, experimental_context=context())
    before = {path: path.read_bytes() for path in first.values()}
    second = execute(project, experimental_context=context(("00220",)))
    assert second["manifest"] != first["manifest"]
    assert {path: path.read_bytes() for path in before} == before
    assert set(json.loads(second["forecast"].read_text())["models"]) == {"00220"}


@pytest.mark.parametrize("codes", [["00220"], [], [110]])
def test_codigos_divergentes_nao_publicam_execucao(project, codes):
    root, _ = project
    with pytest.raises(ForecastError):
        execute(project, codes=codes, experimental_context=context())
    assert not (root / "data/artifacts").exists()


def test_codigo_selecionado_somente_apos_corte_nao_vira_previsao_zero(project):
    root, path = project
    source = root / "data/processed/reviewed.csv"
    content = source.read_text() + "2026-01-11,00330,00005,Grupo Fictício,8,1\n"
    digest = sha256(content.encode()).hexdigest()
    for name in ("canonical.csv", "reviewed.csv"):
        (root / "data/processed" / name).write_text(content)
    preparation_path = root / "data/reports/preparacao-ficticia.json"
    preparation = json.loads(preparation_path.read_text())
    preparation["sha256_dataset"] = digest
    preparation_path.write_text(json.dumps(preparation))
    review = json.loads(path.read_text())
    review.update(sha256_canonico=digest, sha256_revisado=digest, data_referencia="2026-01-11")
    path.write_text(json.dumps(review))
    with pytest.raises(ForecastError, match="Todo código experimental selecionado"):
        execute(project, experimental_context=context(("00110", "00330")))
    assert not (root / "data/artifacts").exists()


def test_avaliacao_posterior_ao_corte_nao_vaza_para_treino_ou_selecao(project, monkeypatch):
    first, cutoff, reference = date(2025, 6, 1), date(2026, 1, 10), date(2026, 1, 17)
    replace_dataset(project, first, reference, cutoff, future=50)
    root, review = project
    actual_evaluator = service.evaluate_product
    train_ends = []

    def evaluator(history, *args, **kwargs):
        train_ends.append(history.index[-1].date())
        return actual_evaluator(history, *args, **kwargs)

    monkeypatch.setattr(service, "evaluate_product", evaluator)
    kwargs = {"cutoff": cutoff, "start": first, "horizon": 7, "experimental_context": context(),
        "model_params": {"random_forest": {"n_estimators": 3}, "hist_gradient_boosting": {"max_iter": 3}}}
    paths = service.run_forecast(root, review, **kwargs)
    result = json.loads(paths["forecast"].read_text())
    report = json.loads(paths["report"].read_text())
    evaluated = report["avaliacao_pos_corte"]["00110"]
    selected = report["evaluations"]["00110"]["selected_model"]
    assert evaluated["status"] == "avaliado" and evaluated["horizonte_completo"]
    assert date.fromisoformat(evaluated["inicio"]) > cutoff
    assert date.fromisoformat(evaluated["fim"]) <= reference
    assert set(evaluated["modelos"]) == {selected, "naive", "seasonal_naive"}
    calendar = service.ManausCalendar((2025, 2026))
    predictions = [Decimal(row["quantidade_prevista"]) for row in result["forecast"]["products"]["00110"]["diaria"]
                   if calendar.is_open(date.fromisoformat(row["periodo_inicio"]))]
    expected_mae = sum(abs(Decimal(50)-value) for value in predictions) / len(predictions)
    assert evaluated["modelos"][selected]["mae"] == pytest.approx(float(expected_mae))
    assert report["evaluations"]["00110"]["test"]["end"] <= cutoff.isoformat()
    replace_dataset(project, first, reference, cutoff, future=900)
    changed = service.run_forecast(root, review, **kwargs)
    changed_report = json.loads(changed["report"].read_text())
    changed_result = json.loads(changed["forecast"].read_text())
    assert train_ends == [cutoff, cutoff]
    assert changed_result["forecast"] == result["forecast"]
    assert changed_report["evaluations"] == report["evaluations"]
    assert changed_report["avaliacao_pos_corte"]["00110"]["modelos"][selected]["mae"] != evaluated["modelos"][selected]["mae"]


def test_avaliacao_pos_corte_para_na_referencia_aprovada_e_identifica_horizonte_parcial(project):
    first, cutoff, reference = date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 13)
    replace_dataset(project, first, reference, cutoff)
    root, review = project
    paths = service.run_forecast(root, review, cutoff=cutoff, start=first, horizon=7,
                                 experimental_context=context())
    evaluation = json.loads(paths["report"].read_text())["avaliacao_pos_corte"]["00110"]
    assert evaluation["status"] == "avaliado"
    assert evaluation["inicio"] > cutoff.isoformat()
    assert evaluation["fim"] == reference.isoformat()
    assert not evaluation["horizonte_completo"]
    assert evaluation["dias_abertos"] == 2


def test_sem_dia_aberto_pos_corte_nao_inventa_metricas(project):
    first, cutoff, reference = date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 11)
    replace_dataset(project, first, reference, cutoff)
    root, review = project
    paths = service.run_forecast(root, review, cutoff=cutoff, start=first, horizon=1,
                                 experimental_context=context())
    evaluation = json.loads(paths["report"].read_text())["avaliacao_pos_corte"]["00110"]
    assert evaluation["status"] == "nao_avaliado" and "modelos" not in evaluation


def test_repository_conserva_contexto_validado_e_rejeita_divergencia():
    source = experimental_bundle()
    result, _, metadata, _, _ = repository._validated(source["result"], source["report"])
    assert metadata["experimento"] == result["experimento"]
    changed = deepcopy(source)
    changed["report"]["experimento"]["estoque"] = "real"
    with pytest.raises(repository.RepositoryError):
        repository.validar_execucao(changed["result"], changed["report"])
    legacy = bundle()
    legacy["report"]["experimento"] = context(("00101", "00102"))
    with pytest.raises(repository.RepositoryError):
        repository.validar_execucao(legacy["result"], legacy["report"])
    changed = deepcopy(source)
    changed["report"]["dataset"]["products"].pop("00101")
    with pytest.raises(repository.RepositoryError):
        repository.validar_execucao(changed["result"], changed["report"])


def test_repository_revalida_sob_transacao_e_preserva_noop_antigo(app, monkeypatch):
    source = experimental_bundle()
    calls = []

    def guard(session, value):
        assert session.in_transaction()
        calls.append(deepcopy(value))

    monkeypatch.setattr(catalog, "validar_selecao_experimental", guard)
    with app.app_context():
        repository.persistir_execucao(**source)
        assert calls == [source["result"]["experimento"]] * 2
        assert repository.carregar_ativa()["report"]["experimento"] == source["result"]["experimento"]
        repository.persistir_execucao(**bundle(2))

        def changed(*args):
            raise catalog.CatalogacaoError("SELECTION_CHANGED", "Seleção fictícia alterada.")

        monkeypatch.setattr(catalog, "validar_selecao_experimental", changed)
        repository.persistir_execucao(**source)  # Replay não reativa execução antiga.
        assert repository.carregar_ativa()["run_id"] == bundle(2)["result"]["run_id"]


def test_repository_reverte_publicacao_se_selecao_muda_antes_de_ativar(app, monkeypatch):
    source = experimental_bundle()
    calls = []

    def guard(*args):
        calls.append(True)
        if len(calls) == 2:
            raise catalog.CatalogacaoError("SELECTION_CHANGED", "Seleção fictícia alterada.")

    monkeypatch.setattr(catalog, "validar_selecao_experimental", guard)
    with app.app_context():
        repository.persistir_execucao(**bundle(2))
        with pytest.raises(repository.RepositoryError) as error:
            repository.persistir_execucao(**source)
        assert error.value.code == "SELECTION_CHANGED"
        assert db.session.get(PrevisaoExecucao, source["result"]["run_id"]) is None
        assert PrevisaoDemanda.query.filter_by(execucao_id=source["result"]["run_id"]).count() == 0
        assert repository.carregar_ativa()["run_id"] == bundle(2)["result"]["run_id"]


def test_cli_snapshot_privado_filtra_sem_expor_codigos(project, monkeypatch, capsys):
    root, review = project
    selected = root / "data/reports/selecao-ficticia.json"
    selected.write_text(json.dumps(context()))
    monkeypatch.setattr(cli, "__file__", str(root / "scripts/forecast_history.py"))
    assert cli.main(["train", "--review", str(review), "--cutoff", "2026-01-10", "--start", "2026-01-01",
        "--horizon", "3", "--experimental-selection", str(selected)]) == 0
    captured = capsys.readouterr()
    assert "00110" not in captured.out + captured.err
    result = json.loads(next((root / "data/artifacts").glob("*/previsoes.json")).read_text())
    assert set(result["models"]) == {"00110"}

