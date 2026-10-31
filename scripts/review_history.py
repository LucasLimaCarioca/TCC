"""Cria o recorte privado depois das confirmações explícitas do operador."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
import sys
from uuid import uuid4

from app.services.preparacao_dados_service import PreparationError, _write_private, private_path
from scripts.consolidate_history import _load


def review_history(project_root, preparation_report, *, cutoff, coverage_complete, exclude_weekends):
    """Preserva o canônico e extremos; não infere cobertura nem preenche lacunas."""
    root = Path(project_root).resolve()
    try:
        reference = date.fromisoformat(cutoff)
        if reference.isoformat() != cutoff or coverage_complete is not True:
            raise PreparationError("Confirme a exportação completa e o último dia completo.")
        if type(exclude_weekends) is not bool:
            raise PreparationError("Informe explicitamente o tratamento dos fins de semana.")
        preparation, fields, rows, provenance = _load(root, preparation_report)
        retained = []
        excluded_future = excluded_weekend = 0
        for row in rows:
            day = date.fromisoformat(row["data"])
            if day > reference:
                excluded_future += 1
            elif exclude_weekends and day.weekday() >= 5:
                excluded_weekend += 1
            else:
                retained.append(row)
        if not retained:
            raise PreparationError("O recorte não contém observações elegíveis.")
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(retained)
        content = buffer.getvalue()
        run_id = "revisao-decisoes-" + uuid4().hex
        directory = private_path(root, "data/processed/" + run_id)
        dataset = private_path(root, directory / "demanda_diaria_revisada.csv")
        report = private_path(root, "data/reports/" + run_id + ".json")
        payload = {
            "status": "revisado_com_recorte", "gerado_em": datetime.now(timezone.utc).isoformat(),
            "data_referencia": cutoff, "politica_data": "dia_fonte", "colunas": list(fields),
            "relatorio_preparacao": provenance["relatorio"],
            "sha256_relatorio_preparacao": provenance["sha256_relatorio"],
            "dataset_canonico": preparation["dataset"], "sha256_canonico": preparation["sha256_dataset"],
            "dataset_revisado": dataset.relative_to(root).as_posix(),
            "sha256_revisado": sha256(content.encode("utf-8")).hexdigest(),
            "contexto": {"exportacao_completa": True},
            "decisoes": {"excluir_fins_de_semana": exclude_weekends,
                         "excluir_posteriores_referencia": True, "preservar_extremos": True,
                         "preencher_lacunas_nesta_etapa": False},
            "linhas_canonicas": len(rows), "linhas_retidas": len(retained),
            "exclusoes_sem_sobreposicao": {"posterior_referencia": excluded_future,
                                           "fim_de_semana": excluded_weekend},
        }
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.mkdir(mode=0o700)
        report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(dataset, content)
        try:
            _write_private(report, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        except OSError:
            dataset.unlink()
            raise
        return {"dataset": dataset, "report": report}
    except PreparationError:
        raise
    except Exception:
        raise PreparationError("Revisão interrompida por falha de leitura ou validação local.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gera revisão privada com cobertura confirmada pelo operador.")
    parser.add_argument("--preparation", required=True)
    parser.add_argument("--cutoff", required=True, help="Último dia completo confirmado, YYYY-MM-DD.")
    parser.add_argument("--confirm-complete", required=True, action="store_true")
    weekends = parser.add_mutually_exclusive_group(required=True)
    weekends.add_argument("--exclude-weekends", action="store_true")
    weekends.add_argument("--keep-weekends", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        result = review_history(root, args.preparation, cutoff=args.cutoff,
                                coverage_complete=args.confirm_complete,
                                exclude_weekends=args.exclude_weekends)
    except PreparationError as error:
        print(f"Revisão interrompida: {error}", file=sys.stderr)
        return 2
    print("Dataset revisado privado:", result["dataset"].relative_to(root))
    print("Revisão privada:", result["report"].relative_to(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
