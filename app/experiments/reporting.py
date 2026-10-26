"""Exportações privadas; nenhuma métrica é calculada a partir de médias de erros."""

import csv
from hashlib import sha256
import io
import json
from pathlib import Path

from app.experiments.errors import ExperimentError
from app.services.preparacao_dados_service import private_path, _write_private


def _csv(fields, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _metrics_rows(forecasts):
    for evidence in forecasts:
        for product in evidence["products"]:
            common = {"execucao_previsao": evidence["run"]["run_id"],
                      "codigo_produto": product["codigo_produto"]}
            def row(stage, model, metric, *, fold=None, train=None, first=None, last=None,
                    open_days=None, reason=None):
                return {**common, "etapa": stage, "modelo": model,
                    "selecionado": model == product["selected_model"], "fold": fold,
                    "avaliado": metric is not None, "treino_inicio": (train or {}).get("start"),
                    "treino_fim": (train or {}).get("end"), "avaliacao_inicio": first,
                    "avaliacao_fim": last, "dias_abertos": open_days,
                    **{key: metric[key] if metric is not None else None
                       for key in ("mae", "rmse", "wape", "smape")},
                    "wape_definida": metric is not None and metric["wape"] is not None,
                    "motivo_sem_avaliacao": reason if metric is None else None}
            for model, candidate in sorted(product["validation"]["candidates"].items()):
                folds = candidate["folds"]
                yield row("validacao_concatenada_dias_abertos", model, candidate["metrics"],
                    first=folds[0]["validation_start"] if folds else None,
                    last=folds[-1]["validation_end"] if folds else None,
                    reason=candidate["reason"])
                for index, fold in enumerate(folds, start=1):
                    yield row("fold_validacao", model, fold["metrics"], fold=index,
                        train={"start": fold["train_start"], "end": fold["train_end"]},
                        first=fold["validation_start"], last=fold["validation_end"],
                        open_days=fold["n_open_days"])
            test = product["test"]
            if test is None:
                yield row("teste_final", product["selected_model"], None,
                          reason=product["selection_reason"])
            else:
                for model, metric in sorted(test["models"].items()):
                    yield row("teste_final", model, metric, train=test["training_interval"],
                              first=test["start"], last=test["end"], open_days=test["n_open_days"])


def _comparison_rows(forecasts):
    for evidence in forecasts:
        for product in evidence["products"]:
            for stage, baselines in product["comparisons"].items():
                for baseline, metrics in baselines.items():
                    for name, comparison in metrics.items():
                        yield {"execucao_previsao": evidence["run"]["run_id"],
                            "codigo_produto": product["codigo_produto"], "etapa": stage,
                            "modelo_selecionado": product["selected_model"], "baseline": baseline,
                            "metrica": name,
                            **{key: value for key, value in comparison.items() if key != "baseline"},
                            "baseline_value": comparison["baseline"]}


def _md(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _summary(report):
    functional = report["functional"]
    lines = ["# Validação experimental do TCC II — relatório privado", "",
        f"Execução: {report['run_id']}", f"Estado técnico: {report['status']}",
        f"Escopo: {report['scope']}", f"Evidência integral da Fase 10: {report['phase10_evidence_complete']}", "",
        "O estado técnico registra a execução dos critérios, não superioridade preditiva.",
        "Este relatório e suas tabelas são restritos a data/.", "", "## Cenários controlados", "",
        "| Caso | Estado |", "|---|---|"]
    lines.extend(f"| {_md(item['id'])} | {_md(item['status'])} |" for item in functional["checks"])
    lines += ["", "## Comunicação local", "",
        "Uma troca request/resposta normalmente gera quatro registros: envio e recebimento de cada mensagem.",
        "Os payloads permanecem omitidos; failure esperado é evidência de rejeição controlada, não falha do experimento.", "",
        "| Ontology | Performative | Estado | Registros |", "|---|---|---|---|"]
    lines.extend(f"| {_md(item['ontology'])} | {_md(item['performative'])} | {_md(item['status'])} | {item['count']} |"
                 for item in (functional.get("communication") or {}).get("counts", []))
    lines += ["", "## Tempos observados", "",
        "São tempos de uma execução por caso completo, incluindo suas verificações; excluem preparação e warmup.",
        "Casos heterogêneos com N=1 não demonstram capacidade, SLA ou desempenho em produção.", "",
        "| Caso | Escopo | N | Duração (s) |", "|---|---|---|---|"]
    lines.extend(f"| {_md(item['name'])} | {_md(item['scope'])} | 1 | {item['seconds']:.6f} |"
                 for item in functional.get("timings", []))
    for evidence in report["forecasts"]:
        lines += ["", "## Avaliação da previsão", "", f"Execução de origem: {evidence['run']['run_id']}",
            "A escolha usa validação temporal; o teste final mede o vencedor e os baselines sem escolher novamente.",
            "O ajuste final da publicação é separado dos treinos anteriores às janelas de avaliação.", "",
            "| Produto | Modelo | Treino antes do teste | Teste | MAE | MAE naive | MAE sazonal | Supera ambos em MAE |",
            "|---|---|---|---|---|---|---|---|"]
        for product in evidence["products"]:
            test = product["test"]
            if test is None:
                cells = [product["codigo_produto"], product["selected_model"], "—", "não avaliado", "—", "—", "—", "não avaliado"]
            else:
                train = test["training_interval"]
                cells = [product["codigo_produto"], product["selected_model"],
                    train["start"] + " a " + train["end"], test["start"] + " a " + test["end"],
                    test["models"][product["selected_model"]]["mae"], test["models"]["naive"]["mae"],
                    test["models"]["seasonal_naive"]["mae"], product["strictly_better_than_both_test_baselines"]]
            lines.append("| " + " | ".join(_md(value) for value in cells) + " |")
        lines += ["", "RMSE, WAPE, sMAPE, todos os folds e comparações de ganho/empate/piora estão nas tabelas CSV e no JSON.",
            "Campos vazios de métricas no CSV significam ausência ou indefinição; não são zero.",
            "A série geral tem somas coerentes, mas não tem métricas de teste disponíveis neste artefato.",
            "Não se calcula média de MAE/RMSE/WAPE/sMAPE entre produtos para substituir essa avaliação."]
    if not report["forecasts"]:
        lines += ["", "Não houve coleta de previsão: este escopo somente funcional não conclui a validação integral da Fase 10."]
    lines += ["", "## Limites de interpretação", "", *["- " + item for item in report["limitations"]], ""]
    return "\n".join(lines)


def export_reports(root, directory, report):
    """Grava arquivos novos 0600; JSON final identifica o pacote concluído."""
    try:
        root = Path(root).resolve()
        directory = private_path(root, directory)
        if not directory.is_relative_to(root / "data/reports"):
            raise ExperimentError("INVALID_PATH")
        functional = report["functional"]
        metric_fields = ["execucao_previsao", "codigo_produto", "etapa", "modelo", "selecionado", "fold",
            "avaliado", "treino_inicio", "treino_fim", "avaliacao_inicio", "avaliacao_fim", "dias_abertos",
            "mae", "rmse", "wape", "smape", "wape_definida", "motivo_sem_avaliacao"]
        comparison_fields = ["execucao_previsao", "codigo_produto", "etapa", "modelo_selecionado", "baseline", "metrica",
            "selected", "baseline_value", "outcome", "absolute_error_reduction", "relative_error_reduction_percent",
            "relative_unavailable_reason", "difference_unit"]
        contents = {
            "cenarios.csv": _csv(["id", "status", "detail"],
                ({key: check.get(key) for key in ("id", "status", "detail")} for check in functional["checks"])),
            "mensagens.csv": _csv(["ontology", "performative", "status", "count"],
                (functional.get("communication") or {}).get("counts", [])),
            "tempos.csv": _csv(["name", "scope", "seconds"], functional.get("timings", [])),
            "metricas.csv": _csv(metric_fields, _metrics_rows(report["forecasts"])),
            "comparacoes.csv": _csv(comparison_fields, _comparison_rows(report["forecasts"])),
            "resumo.md": _summary(report),
        }
        for name, content in contents.items():
            _write_private(private_path(root, directory / name), content)
        report["export_sha256"] = {name: sha256(content.encode()).hexdigest() for name, content in contents.items()}
        target = private_path(root, directory / "relatorio.json")
        _write_private(target, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        return {"report": target, "summary": directory / "resumo.md", "directory": directory}
    except ExperimentError:
        raise
    except Exception:
        raise ExperimentError("REPORT_FAILED") from None
