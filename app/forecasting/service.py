"""Execuções auditáveis do motor, exclusivamente em arquivos privados locais."""

import csv
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from importlib.metadata import version
from html import escape
import io
import json
import os
from pathlib import Path
import pickle
import platform
from uuid import uuid4

from app.forecasting.aggregation import aggregate_forecasts
from app.forecasting.calendar import ManausCalendar
from app.forecasting.dataset import build_series
from app.forecasting.errors import ForecastError
from app.forecasting.evaluation import evaluate_product
from app.services.preparacao_dados_service import OUTPUT_FIELDS, private_path, _write_private


ENGINE_VERSION = "forecast-v1"


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
    raise ForecastError("Informe datas comerciais no formato YYYY-MM-DD.")


def _integer(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ForecastError("Parâmetro de execução fora dos limites suportados.")
    return value


def _file(root, candidate, directory):
    path = private_path(root, candidate)
    if not path.is_relative_to(root / "data" / directory) or not path.is_file() or path.stat().st_nlink != 1:
        raise ForecastError("Arquivo privado ausente, fora do diretório previsto ou com vínculos adicionais.")
    return path


def _json(path):
    def invalid(_):
        raise ForecastError("O artefato contém um valor JSON inválido.")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid)


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def load_review(root, report_path, cutoff):
    """Confere a cadeia preparação → revisão sem reler o CSV bruto."""
    root, cutoff = Path(root).resolve(), _day(cutoff)
    path = _file(root, report_path, "reports")
    review = _json(path)
    if (not isinstance(review, dict) or review.get("status") != "revisado_com_recorte"
            or review.get("politica_data") != "dia_fonte"
            or review.get("colunas") != list(OUTPUT_FIELDS)
            or not isinstance(review.get("contexto"), dict)
            or review["contexto"].get("exportacao_completa") is not True):
        raise ForecastError("Exige revisão concluída, dia_fonte e exportação completa confirmada.")
    if cutoff > _day(review.get("data_referencia")):
        raise ForecastError("A data de corte não pode superar a referência da revisão aprovada.")
    preparation_path = _file(root, review["relatorio_preparacao"], "reports")
    preparation = _json(preparation_path)
    if (not isinstance(preparation, dict) or preparation.get("status") != "aprovado"
            or preparation.get("versao_pipeline") != "2" or preparation.get("erros") != {}
            or preparation.get("politica_data") != "dia_fonte"
            or preparation.get("colunas_dataset") != list(OUTPUT_FIELDS)
            or preparation.get("dataset") != review.get("dataset_canonico")
            or preparation.get("sha256_dataset") != review.get("sha256_canonico")):
        raise ForecastError("Revisão incompatível com o relatório da preparação.")
    canonical = _file(root, review["dataset_canonico"], "processed")
    if sha256(canonical.read_bytes()).hexdigest() != review["sha256_canonico"]:
        raise ForecastError("O dataset canônico foi alterado após a revisão.")
    dataset = _file(root, review["dataset_revisado"], "processed")
    content = dataset.read_bytes()
    if sha256(content).hexdigest() != review.get("sha256_revisado"):
        raise ForecastError("O recorte revisado foi alterado após a aprovação.")
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    if tuple(reader.fieldnames or ()) != OUTPUT_FIELDS:
        raise ForecastError("O recorte deve conter somente as seis colunas diárias autorizadas.")
    rows = list(reader)
    if not rows:
        raise ForecastError("O recorte revisado não contém observações.")
    # Não copia textos livres do relatório de origem para os artefatos.
    provenance = {
        "review": path.relative_to(root).as_posix(),
        "review_sha256": sha256(path.read_bytes()).hexdigest(),
        "preparation": preparation_path.relative_to(root).as_posix(),
        "dataset": dataset.relative_to(root).as_posix(),
        "dataset_sha256": sha256(content).hexdigest(),
        "canonical_dataset_sha256": review["sha256_canonico"],
        "review_reference": review["data_referencia"],
        "coverage_complete": True, "date_policy": "dia_fonte",
    }
    return rows, provenance


def load_calendar_overrides(root, path):
    if path is None:
        return {}
    data = _json(_file(Path(root).resolve(), path, "artifacts"))
    if not isinstance(data, dict) or set(data) != {"overrides"} or not isinstance(data["overrides"], list):
        raise ForecastError("Arquivo de exceções do calendário inválido.")
    result = {}
    for item in data["overrides"]:
        if not isinstance(item, dict) or set(item) != {"date", "is_open"} or type(item["is_open"]) is not bool:
            raise ForecastError("Exceção operacional inválida.")
        day = _day(item["date"])
        if day in result:
            raise ForecastError("O calendário contém exceções repetidas.")
        result[day] = item["is_open"]
    return result


def _comparison(evaluation):
    test = evaluation["test"]
    if test is None:
        return {"status": "nao_avaliado", "reason": evaluation["selection_reason"]}
    selected = test["models"][evaluation["selected_model"]]["mae"]
    baselines = {name: test["models"][name]["mae"] for name in ("naive", "seasonal_naive")}
    return {
        "status": "avaliado_no_teste_final", "selected_mae": selected,
        "baseline_mae": baselines,
        "strictly_better_than_both_baselines": selected < min(baselines.values()),
        "note": "O teste final mede o vencedor da validação; não altera a seleção.",
    }


def _summary(report):
    def cell(value):
        return escape(str(value)).replace("|", "\\|").replace("\n", " ")
    def score(value):
        return "não avaliado" if value is None else format(value, ".6g")
    lines = ["# Previsão de demanda — relatório privado", "",
             "Dados restritos: manter em data/, fora do Git e de serviços externos.", "",
             "- Versão: " + ENGINE_VERSION,
             "- Corte do histórico: " + report["cutoff"],
             "- Início solicitado: " + (report["start"] or "primeira observação elegível"),
             "- Horizonte em dias de calendário: " + str(report["horizon_days"]), "",
             "## Avaliação", "",
             "A validação rolling origin escolhe pela MAE nos dias abertos. O teste final permanece separado e apenas mede o vencedor e os baselines. O modelo é então reajustado em todo o histórico disponível para a previsão futura.", "",
             "MAE/RMSE usam a unidade de quantidade do produto. WAPE/sMAPE são percentuais; WAPE sem demanda real pode ser indefinido. Baseline em série curta sem avaliação é indicado explicitamente.", "",
             "| Código | Modelo escolhido | MAE validação | MAE teste | MAE naive teste | MAE sazonal teste | Supera ambos no teste? |",
             "|---|---|---:|---:|---:|---:|---|"]
    for code, evaluation in report["evaluations"].items():
        selected = evaluation["selected_model"]
        validation = evaluation["validation"]["candidates"][selected]["metrics"]
        comparison = evaluation["test_comparison"]
        tested = comparison["status"] == "avaliado_no_teste_final"
        values = [code, selected, score(validation["mae"] if validation else None),
                  score(comparison.get("selected_mae")), score(comparison.get("baseline_mae", {}).get("naive")),
                  score(comparison.get("baseline_mae", {}).get("seasonal_naive")),
                  ("sim" if comparison["strictly_better_than_both_baselines"] else "não") if tested else "não avaliado"]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines += ["", "## Interpretação e rastreabilidade", "",
              "- Dataset revisado: " + report["provenance"]["dataset"],
              "- Séries diárias usadas: " + report["series_file"],
              "- Previsões por produto e geral: " + report["forecast_file"],
              "- Versão de calendário: " + report["calendar"]["version"],
              "- Anos, feriados, fontes oficiais, exclusões e zeros estão detalhados no JSON desta execução.",
              "- As somas semanal/mensal e geral derivam da mesma previsão diária quantizada; períodos parciais são identificados.", ""]
    lines.extend("- " + limitation for limitation in report["limitations"])
    return "\n".join(lines) + "\n"


def run_forecast(project_root, review_path, *, cutoff, start=None, horizon=28,
                 test_days=14, validation_days=14, folds=3, codes=None,
                 calendar_overrides=None, model_params=None, progress=None):
    """Treina, avalia e publica um manifesto privado somente ao concluir tudo.

    Não escreve no SQLite, não inicializa agentes nem altera histórico/revisão.
    Execuções interrompidas não possuem manifesto de conclusão consultável.
    """
    try:
        root = Path(project_root).resolve()
        cutoff, start = _day(cutoff), _day(start) if start is not None else None
        horizon = _integer(horizon, 366)
        test_days, validation_days = _integer(test_days, 366), _integer(validation_days, 366)
        folds = _integer(folds, 10)
        if start is not None and start > cutoff:
            raise ForecastError("O início do histórico deve ser anterior à data de corte.")
        rows, provenance = load_review(root, review_path, cutoff)
        requested_codes = None
        if codes is not None:
            if not isinstance(codes, (tuple, list)) or not codes or any(
                not isinstance(code, str) or not code or code != code.strip() or len(code) > 64 for code in codes
            ):
                raise ForecastError("Seleção de códigos externos inválida.")
            requested_codes = set(codes)
            if not requested_codes <= {row["codigo_produto"] for row in rows}:
                raise ForecastError("A seleção contém código ausente do recorte revisado.")
        excluded_not_selected = sum(requested_codes is not None and row["codigo_produto"] not in requested_codes for row in rows)
        rows = [row for row in rows if requested_codes is None or row["codigo_produto"] in requested_codes]
        excluded_before_start = sum(start is not None and _day(row["data"]) < start for row in rows)
        rows = [row for row in rows if start is None or _day(row["data"]) >= start]
        if not rows:
            raise ForecastError("Não há observações no intervalo e seleção solicitados.")
        first = min(_day(row["data"]) for row in rows)
        last_forecast = cutoff + timedelta(days=horizon)
        calendar = ManausCalendar(range(min(first.year, cutoff.year), last_forecast.year + 1),
                                  overrides=load_calendar_overrides(root, calendar_overrides))
        dataset = build_series(rows, calendar, cutoff, coverage_complete=True)
        if not dataset["series"]:
            raise ForecastError("Nenhum produto possui observações elegíveis para o calendário e corte.")
        dataset["report"]["excluded_before_start"] = excluded_before_start
        dataset["report"]["excluded_not_selected"] = excluded_not_selected
        dataset["report"]["requested_codes"] = sorted(requested_codes) if requested_codes else None
        dataset["report"]["requested_start"] = start.isoformat() if start else None
        run_id = "previsao-" + uuid4().hex
        artifacts = private_path(root, "data/artifacts/" + run_id)
        processed = private_path(root, "data/processed/" + run_id)
        reports = private_path(root, "data/reports")
        for directory in (artifacts, processed, reports):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        file_hashes = {}

        def save_text(path, content):
            path = private_path(root, path)
            _write_private(path, content)
            file_hashes[path.relative_to(root).as_posix()] = sha256(content.encode("utf-8")).hexdigest()
            return path

        started = datetime.now(timezone.utc).isoformat()
        evaluations, predictions, model_files = {}, {}, {}
        for position, (code, history) in enumerate(dataset["series"].items(), 1):
            evaluation = evaluate_product(history, calendar, horizon=horizon, test_days=test_days,
                                          validation_days=validation_days, folds=folds, model_params=model_params)
            predictions[code] = evaluation["forecast"]
            evaluations[code] = {key: value for key, value in evaluation.items() if key not in {"forecast", "forecaster"}}
            evaluations[code]["test_comparison"] = _comparison(evaluation)
            estimator = evaluation["forecaster"].estimator_
            evaluations[code]["model_parameters"] = (estimator.get_params(deep=False) if estimator is not None
                else {"season": 7} if evaluation["selected_model"] == "seasonal_naive" else {})
            model_path = private_path(root, artifacts / f"modelo-{position:05d}.pkl")
            content = pickle.dumps(evaluation["forecaster"], protocol=pickle.HIGHEST_PROTOCOL)
            descriptor = os.open(model_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            relative = model_path.relative_to(root).as_posix()
            file_hashes[relative] = sha256(content).hexdigest()
            model_files[code] = relative
            if progress is not None:
                progress()  # Somente sinal de progresso; nunca fornece código ou observação.

        aggregation = aggregate_forecasts(predictions)
        created = datetime.now(timezone.utc).isoformat()
        result = {"version": ENGINE_VERSION, "run_id": run_id, "generated_at": created,
                  "cutoff": cutoff.isoformat(), "horizon_days": horizon,
                  "forecast": aggregation, "models": {code: item["selected_model"] for code, item in evaluations.items()}}
        result_path = save_text(artifacts / "previsoes.json", _json_text(result))
        training = io.StringIO(newline="")
        writer = csv.writer(training, lineterminator="\n")
        writer.writerow(("data", "codigo_produto", "quantidade", "dia_aberto"))
        for code, history in dataset["series"].items():
            for day, value in history.items():
                writer.writerow((day.date().isoformat(), code, repr(float(value)), int(calendar.is_open(day.date()))))
        training_path = save_text(processed / "series_diarias.csv", training.getvalue())
        report = {"version": ENGINE_VERSION, "run_id": run_id, "started_at": started, "generated_at": created,
                  "provenance": provenance, "cutoff": cutoff.isoformat(), "start": start.isoformat() if start else None,
                  "horizon_days": horizon, "evaluation_config": {"test_days": test_days,
                  "validation_days": validation_days, "folds": folds, "selection_metric": "mae_open_days",
                  "model_params": model_params}, "dataset": dataset["report"], "calendar": calendar.metadata(),
                  "evaluations": evaluations, "forecast_file": result_path.relative_to(root).as_posix(),
                  "series_file": training_path.relative_to(root).as_posix(),
                  "limitations": ["Zero em dia aberto sem registro depende da completude confirmada da exportação.",
                      "Não se inventa demanda antes do primeiro registro elegível de cada produto.",
                      "A seleção usa validação, e a comparação final pode não superar os baselines.",
                      "Projeções são condicionais ao calendário e ao horizonte publicados, com datas e parâmetros explícitos.",
                      "O geral soma quantidades comerciais heterogêneas; não representa litros, peso ou faturamento.",
                      "Agente XMPP e persistência em PrevisaoDemanda permanecem para a Fase 7."]}
        report_path = save_text(reports / (run_id + ".json"), _json_text(report))
        summary_path = save_text(reports / (run_id + ".md"), _summary(report))
        code_root = Path(__file__).resolve().parents[2]
        code_files = list(Path(__file__).resolve().parent.glob("*.py"))
        code_files.append(code_root / "scripts/forecast_history.py")
        manifest = {"status": "concluido", "version": ENGINE_VERSION, "run_id": run_id,
                    "generated_at": created, "forecast_file": result_path.relative_to(root).as_posix(),
                    "report_file": report_path.relative_to(root).as_posix(),
                    "summary_file": summary_path.relative_to(root).as_posix(), "models": model_files,
                    "versions": {name: version(name) for name in ("numpy", "pandas", "scikit-learn", "threadpoolctl")},
                    "source_sha256": {path.relative_to(code_root).as_posix(): sha256(path.read_bytes()).hexdigest()
                                      for path in sorted(code_files) if path.is_file()},
                    "python": platform.python_version(), "files_sha256": file_hashes}
        # Este é o único indicador de publicação: gravado por último.
        manifest_path = private_path(root, artifacts / "manifest.json")
        _write_private(manifest_path, _json_text(manifest))
        return {"manifest": manifest_path, "report": report_path, "summary": summary_path,
                "forecast": result_path, "series": training_path}
    except ForecastError:
        raise
    except Exception:
        raise ForecastError("Execução interrompida por falha local; artefatos sem manifesto concluído não são resultados publicados.") from None


def consultar_previsao(project_root, manifest_path, *, codigo_produto=None, granularidade="diaria"):
    """Consulta artefatos JSON íntegros, sem desserializar modelos pickle."""
    try:
        root = Path(project_root).resolve()
        if granularidade not in {"diaria", "semanal", "mensal"}:
            raise ForecastError("Granularidade de previsão inválida.")
        path = _file(root, manifest_path, "artifacts")
        manifest = _json(path)
        if (not isinstance(manifest, dict) or manifest.get("status") != "concluido"
                or manifest.get("version") != ENGINE_VERSION):
            raise ForecastError("A execução não possui um manifesto de conclusão compatível.")
        forecast_path = _file(root, manifest["forecast_file"], "artifacts")
        if not forecast_path.is_relative_to(path.parent):
            raise ForecastError("A previsão não pertence à execução consultada.")
        content = forecast_path.read_bytes()
        if sha256(content).hexdigest() != manifest["files_sha256"].get(forecast_path.relative_to(root).as_posix()):
            raise ForecastError("A previsão foi alterada após a conclusão da execução.")
        data = _json(forecast_path)
        if data.get("version") != ENGINE_VERSION or data.get("run_id") != manifest.get("run_id"):
            raise ForecastError("Previsão incompatível com o manifesto.")
        selected = data["forecast"]["overall"] if codigo_produto is None else data["forecast"]["products"].get(codigo_produto)
        if selected is None:
            raise ForecastError("Código externo ausente desta previsão.")
        return {"codigo_produto": codigo_produto, "granularidade": granularidade,
                "periodos": selected[granularidade], "total_previsto": selected["total_previsto"],
                "gerada_em": data["generated_at"], "data_corte": data["cutoff"],
                "horizonte_dias": data["horizon_days"], "versao": ENGINE_VERSION,
                "modelo": data["models"] if codigo_produto is None else data["models"][codigo_produto]}
    except ForecastError:
        raise
    except Exception:
        raise ForecastError("Não foi possível consultar o artefato privado de previsão.") from None
