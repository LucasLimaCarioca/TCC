"""Consolida datasets preparados; a fonte prioritária substitui dias inteiros."""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
from uuid import uuid4

from app.services.importacao_service import _read_dataset
from app.services.preparacao_dados_service import (
    DATASET_SCHEMAS, PreparationError, _write_private, private_path,
)


def _load(root, report_path):
    path = private_path(root, report_path)
    if not path.is_relative_to(root / "data/reports") or not path.is_file() or path.stat().st_nlink != 1:
        raise PreparationError("Informe um relatório privado de preparação existente.")
    report = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(report, dict) or report.get("status") != "aprovado"
            or report.get("politica_data") != "dia_fonte" or report.get("erros") != {}
            or report.get("versao_pipeline") not in DATASET_SCHEMAS):
        raise PreparationError("A consolidação exige preparações aprovadas com dia_fonte.")
    fields = DATASET_SCHEMAS[report["versao_pipeline"]]
    if report.get("colunas_dataset") != list(fields):
        raise PreparationError("As colunas não correspondem à versão da preparação.")
    dataset = private_path(root, report["dataset"])
    if not dataset.is_relative_to(root / "data/processed") or not dataset.is_file() or dataset.stat().st_nlink != 1:
        raise PreparationError("Dataset privado ausente ou com vínculos adicionais.")
    content = dataset.read_bytes()
    if sha256(content).hexdigest() != report.get("sha256_dataset"):
        raise PreparationError("Dataset alterado após a preparação.")
    errors, stats = Counter(), Counter()
    _read_dataset(content, stats, errors, fields)
    if errors or stats["duplicatas_no_dataset"]:
        raise PreparationError("Dataset com inconsistências; revise a preparação.")
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    rows = list(reader)
    provenance = {
        "relatorio": path.relative_to(root).as_posix(),
        "sha256_relatorio": sha256(path.read_bytes()).hexdigest(),
        "dataset": dataset.relative_to(root).as_posix(),
        "sha256_dataset": sha256(content).hexdigest(),
    }
    return report, fields, rows, provenance


def consolidate_history(project_root, base_report, priority_report):
    """Não soma sobreposições: elimina da base todos os dias da fonte prioritária.

    Até produtos ausentes no arquivo prioritário ficam de fora no dia substituído.
    Isso evita manter vendas antigas de uma exportação que foi corrigida por dia.
    A aprovação técnica resultante não substitui a revisão para treinamento.
    """
    root = Path(project_root).resolve()
    try:
        base, fields, base_rows, base_source = _load(root, base_report)
        priority, priority_fields, priority_rows, priority_source = _load(root, priority_report)
        if fields != priority_fields:
            raise PreparationError("Reprepare as fontes com a mesma versão antes de consolidar.")
        if base_source["sha256_dataset"] == priority_source["sha256_dataset"]:
            raise PreparationError("Os dois datasets são idênticos; não há continuação para consolidar.")
        priority_days = {row["data"] for row in priority_rows}
        shared_days = sorted(priority_days & {row["data"] for row in base_rows})
        kept = [row for row in base_rows if row["data"] not in priority_days]
        rows = sorted(kept + priority_rows, key=lambda row: (row["data"], row["codigo_produto"]))
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        content = output.getvalue()
        errors, stats = Counter(), Counter()
        _read_dataset(content.encode("utf-8"), stats, errors, fields)
        if errors or stats["duplicatas_no_dataset"]:
            raise PreparationError("As fontes combinadas têm metadados ou observações inconsistentes.")

        # O hash de fonte identifica o manifesto da composição, não um CSV bruto.
        sources = {"base": base_source, "prioritaria": priority_source,
                   "politica_sobreposicao": "substituir_dia_inteiro"}
        source_text = json.dumps(sources, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        run_id = "consolidacao-" + uuid4().hex
        directory = private_path(root, "data/processed/" + run_id)
        reports = private_path(root, "data/reports")
        dataset = private_path(root, directory / "demanda_diaria.csv")
        report_path = private_path(root, reports / (run_id + ".json"))
        days = {row["data"] for row in rows}
        report = {
            "tipo_fonte": "composicao_de_datasets_preparados",
            "versao_pipeline": base["versao_pipeline"],
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "status": "aprovado", "politica_data": "dia_fonte", "erros": {},
            "colunas_dataset": list(fields), "dataset": dataset.relative_to(root).as_posix(),
            "sha256_dataset": sha256(content.encode("utf-8")).hexdigest(),
            "sha256_fonte": sha256(source_text.encode("utf-8")).hexdigest(),
            "composicao": sources, "dias_substituidos": shared_days,
            "linhas_base_substituidas": len(base_rows) - len(kept),
            "numero_linhas_agregadas": len(rows), "numero_dias_observados": len(days),
            "intervalo_temporal": {"inicio": min(days), "fim": max(days)},
            "revisao_para_treinamento": "pendente",
        }
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.mkdir(mode=0o700)
        reports.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(dataset, content)
        try:
            _write_private(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        except OSError:
            dataset.unlink()
            raise
        return {"dataset": dataset, "report": report_path}
    except PreparationError:
        raise
    except Exception:
        raise PreparationError("Consolidação interrompida por falha de leitura ou validação local.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Consolida duas preparações sem duplicar dias.")
    parser.add_argument("--base", required=True, help="Relatório aprovado do histórico anterior.")
    parser.add_argument("--priority", required=True, help="Relatório que prevalece em cada dia compartilhado.")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        result = consolidate_history(root, args.base, args.priority)
    except PreparationError as error:
        print(f"Consolidação interrompida: {error}", file=sys.stderr)
        return 2
    print("Dataset privado:", result["dataset"].relative_to(root))
    print("Relatório privado:", result["report"].relative_to(root))
    print("Consolidação concluída. A revisão para treinamento permanece separada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
