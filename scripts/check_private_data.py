"""Bloqueia caminhos de dados restritos no índice Git sem ler seus conteúdos."""

from pathlib import PurePosixPath
import subprocess
import sys


PRIVATE_DIRS = {"data", "artifacts", "reports", "exports", "secrets", "instance"}
PRIVATE_SUFFIXES = {
    ".csv", ".tsv", ".parquet", ".feather", ".arrow", ".jsonl", ".ndjson",
    ".xlsx", ".xls", ".db", ".sqlite", ".sqlite3", ".pkl", ".pickle", ".joblib", ".key",
}


def is_private_path(filename):
    path = PurePosixPath(filename.lower())
    if any(part in PRIVATE_DIRS for part in path.parts[:-1]):
        return True
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return True
    # suffixes também reconhece cópias compactadas, como pedidos.csv.gz.
    if PRIVATE_SUFFIXES.intersection(path.suffixes):
        return True
    return any(suffix.startswith((".db-", ".sqlite-", ".sqlite3-")) for suffix in path.suffixes)


def main():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "-z"], capture_output=True, check=False,
    )
    if result.returncode:
        print("Não foi possível verificar o índice Git; commit bloqueado.", file=sys.stderr)
        return 2
    filenames = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    blocked = [name for name in filenames if name and is_private_path(name)]
    if blocked:
        # Não imprime conteúdo nem nomes que possam conter identificadores.
        print(
            f"Commit bloqueado: {len(blocked)} arquivo(s) em caminhos de dados restritos. "
            "Retire-os do índice Git e mantenha os dados locais em data/.",
            file=sys.stderr,
        )
        return 1
    print("Proteção de dados: nenhum caminho restrito no índice Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
