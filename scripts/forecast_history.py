"""Motor Fase 6: treinamento e consulta locais, sem iniciar a aplicação web."""

import argparse
from pathlib import Path
import sys

from app.forecasting.errors import ForecastError
from app.forecasting.service import consultar_previsao, run_forecast
from app.services.preparacao_dados_service import _write_private, private_path
import json
from uuid import uuid4


def main(argv=None):
    parser = argparse.ArgumentParser(description="Avalia e consulta previsões locais com dados privados.")
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train", help="Executa avaliação temporal e previsão.")
    train.add_argument("--review", required=True, help="JSON privado da revisão de qualidade concluída.")
    train.add_argument("--cutoff", required=True, help="Último dia completo conhecido, YYYY-MM-DD.")
    train.add_argument("--start", help="Início do recorte de histórico, YYYY-MM-DD.")
    train.add_argument("--horizon", type=int, default=28)
    train.add_argument("--test-days", type=int, default=14)
    train.add_argument("--validation-days", type=int, default=14)
    train.add_argument("--folds", type=int, default=3)
    train.add_argument("--code", action="append", help="Código externo opcional, repetível.")
    train.add_argument("--calendar-overrides", help="JSON privado de exceções operacionais em data/artifacts/.")
    query = commands.add_parser("query", help="Salva uma consulta de previsão em relatório privado.")
    query.add_argument("--manifest", required=True)
    query.add_argument("--code")
    query.add_argument("--granularity", choices=("diaria", "semanal", "mensal"), default="diaria")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "train":
            result = run_forecast(root, args.review, cutoff=args.cutoff, start=args.start,
                                  horizon=args.horizon, test_days=args.test_days,
                                  validation_days=args.validation_days, folds=args.folds,
                                  codes=args.code, calendar_overrides=args.calendar_overrides)
            print("Manifesto privado:", result["manifest"].relative_to(root))
            print("Relatório privado:", result["report"].relative_to(root))
            print("Resumo privado:", result["summary"].relative_to(root))
        else:
            result = consultar_previsao(root, args.manifest, codigo_produto=args.code,
                                       granularidade=args.granularity)
            directory = private_path(root, "data/reports")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = private_path(root, directory / ("consulta-previsao-" + uuid4().hex + ".json"))
            _write_private(target, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            print("Consulta privada:", target.relative_to(root))
    except ForecastError as error:
        print(f"Previsão interrompida: {error}", file=sys.stderr)
        return 2
    except Exception:
        print("Previsão interrompida por falha local de validação ou gravação.", file=sys.stderr)
        return 2
    print("Operação concluída; resultados permanecem restritos a data/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
