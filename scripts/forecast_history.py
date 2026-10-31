"""Motor Fase 6: treinamento e consulta locais, sem iniciar a aplicação web."""

import argparse
from pathlib import Path
import sys

from app.forecasting.errors import ForecastError
from app.forecasting.experimental import validate_experimental_context
from app.forecasting.service import consultar_previsao, run_forecast
from app.services.preparacao_dados_service import _write_private, private_path
import json
from uuid import uuid4


def main(argv=None):
    parser = argparse.ArgumentParser(description="Avalia e consulta previsões locais com dados privados.")
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train", help="Treina somente os códigos de uma seleção experimental explícita.")
    train.add_argument("--review", required=True, help="JSON privado da revisão de qualidade concluída.")
    train.add_argument("--cutoff", required=True, help="Último dia completo conhecido, YYYY-MM-DD.")
    train.add_argument("--start", help="Início do recorte de histórico, YYYY-MM-DD.")
    train.add_argument("--horizon", type=int, default=28)
    train.add_argument("--test-days", type=int, default=14)
    train.add_argument("--validation-days", type=int, default=14)
    train.add_argument("--folds", type=int, default=3)
    train.add_argument("--code", action="append", help="Código externo opcional, repetível.")
    train.add_argument("--calendar-overrides", help="JSON privado de exceções operacionais em data/artifacts/.")
    train.add_argument("--experimental-selection",
        help="Snapshot experimental privado em data/reports/ ou data/artifacts/; não confirma vínculos no banco.")
    query = commands.add_parser("query", help="Salva uma consulta de previsão em relatório privado.")
    query.add_argument("--manifest", required=True)
    query.add_argument("--code")
    query.add_argument("--granularity", choices=("diaria", "semanal", "mensal"), default="diaria")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "train":
            if not args.experimental_selection:
                raise ForecastError("Exporte a seleção auditada com previsao exportar-selecao e informe --experimental-selection antes de treinar.")
            experimental = None
            if args.experimental_selection:
                path = private_path(root, args.experimental_selection)
                if (not any(path.is_relative_to(root / "data" / folder) for folder in ("reports", "artifacts"))
                        or not path.is_file() or path.stat().st_nlink != 1):
                    raise ForecastError("Snapshot experimental fora do diretório privado permitido.")
                try:
                    experimental = validate_experimental_context(json.loads(path.read_text(encoding="utf-8")))
                except (ValueError, OSError, UnicodeError):
                    raise ForecastError("Snapshot experimental inválido.") from None
            result = run_forecast(root, args.review, cutoff=args.cutoff, start=args.start,
                                  horizon=args.horizon, test_days=args.test_days,
                                  validation_days=args.validation_days, folds=args.folds,
                                  codes=args.code, calendar_overrides=args.calendar_overrides,
                                  experimental_context=experimental)
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
