"""Executar da raiz: python -m scripts.import_history --report data/reports/....json."""

import argparse
from pathlib import Path
import sys

from app.app import create_app
from app.database import db
from app.services.importacao_service import ImportacaoError, importar_historico


def main(argv=None):
    parser = argparse.ArgumentParser(description="Importa demanda diária preparada, sem dados pessoais.")
    parser.add_argument("--report", required=True, help="Relatório aprovado da preparação em data/reports/.")
    parser.add_argument("--validar-apenas", action="store_true", help="Valida e gera relatório sem escrever no banco.")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        app = create_app()
        with app.app_context():
            try:
                result = importar_historico(root, args.report, validar_apenas=args.validar_apenas)
            finally:
                db.session.remove()
                db.engine.dispose()
    except ImportacaoError as error:
        print(f"Importação interrompida: {error}", file=sys.stderr)
        return 2
    except Exception:
        print("Importação interrompida por falha na configuração local.", file=sys.stderr)
        return 2
    print("Relatório privado:", result["report"].relative_to(root))
    if result["status"] == "bloqueado":
        print("Importação bloqueada: revise o relatório; nenhum registro foi inserido.")
        return 1
    if args.validar_apenas:
        print("Validação concluída; nenhum registro foi inserido.")
    else:
        print("Importação concluída. Histórico e relatórios permanecem privados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
