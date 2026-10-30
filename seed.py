"""Prepara exclusivamente o banco separado de demonstração, mediante --demo."""

import argparse

from app.services.demonstracao_service import (
    DemonstracaoError,
    create_demo_app,
    preparar_demonstracao,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", required=True,
                        help="Criar somente data/artifacts/demonstracao.db, com dados fictícios.")
    parser.parse_args(argv)
    try:
        result = preparar_demonstracao()
    except DemonstracaoError as error:
        parser.exit(1, f"Demonstração não preparada: {error}\n")
    print("Banco de demonstração separado preparado em data/artifacts/demonstracao.db.")
    print(f"Novos produtos fictícios: {result['produtos_criados']}; novos clientes fictícios: {result['clientes_criados']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
