"""CLI da Fase 10; console sem códigos, métricas ou conteúdos históricos."""

import argparse
from pathlib import Path
import sys

from app.experiments.errors import ExperimentError
from app.experiments.service import run_validation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validação experimental local do TCC II com relatórios privados.")
    parser.add_argument("--manifest", action="append", default=[],
                        help="Manifesto privado de uma previsão concluída; pode ser repetido até dez vezes.")
    parser.add_argument("--functional-only", action="store_true",
                        help="Executa apenas os cenários fictícios, sem concluir a avaliação de previsão.")
    args = parser.parse_args(argv)
    if bool(args.manifest) == args.functional_only:
        parser.error("informe --manifest ou --functional-only, exclusivamente")
    try:
        result = run_validation(PROJECT_ROOT, args.manifest, functional_only=args.functional_only)
        print("Relatório privado:", result["report"].relative_to(PROJECT_ROOT))
        print("Resumo privado:", result["summary"].relative_to(PROJECT_ROOT))
        print("Estado técnico: " + result["status"] + "; evidências restritas a data/.")
        if not result["evidence_complete"]:
            print("Este pacote não conclui a evidência integral da Fase 10.")
        return 0 if result["status"] == "aprovado" else 2
    except ExperimentError as error:
        print("Validação interrompida: " + error.code + ". " + str(error), file=sys.stderr)
    except Exception:
        print("Validação interrompida por falha local; nenhum conteúdo histórico foi exibido.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
