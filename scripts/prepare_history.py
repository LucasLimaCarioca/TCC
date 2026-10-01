"""Executar da raiz: python -m scripts.prepare_history --date-policy dia_fonte."""

import argparse
from pathlib import Path
import sys

from app.services.preparacao_dados_service import DATE_POLICIES, PreparationError, prepare_history


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepara demanda diária local sem dados pessoais.")
    parser.add_argument("--input", default="data/pedido.csv", help="CSV UTF-8, ignorado pelo Git, dentro de data/.")
    parser.add_argument("--date-policy", choices=DATE_POLICIES, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        result = prepare_history(root, args.input, date_policy=args.date_policy)
    except PreparationError as error:
        print(f"Preparação interrompida: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, ArithmeticError):
        # Exceções da biblioteca podem conter nomes/valores; não expor traceback.
        print("Preparação interrompida por falha de leitura, validação ou gravação local.", file=sys.stderr)
        return 2
    # Só caminhos gerados pelo programa são exibidos; nenhum dado da fonte.
    print("Relatório local:", result["report"].relative_to(root))
    if result["status"] != "aprovado":
        print("Dataset bloqueado: o relatório contém inconsistências que exigem revisão.")
        return 1
    print("Dataset local:", result["dataset"].relative_to(root))
    print("Preparação concluída. Os arquivos permanecem restritos à pasta data/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
