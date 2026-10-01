"""Valida a proteção usando somente caminhos e dados fictícios."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.check_private_data import is_private_path


@pytest.mark.parametrize("filename", [
    "data/pedidos.csv", "data/processed/demanda.json", "data/reports/qualidade.html",
    "data/artifacts/modelo.bin", "exports/copia.json", "reports/grafico.png",
    "instance/sorvetes.db", "secrets/hmac.txt", "copia.CSV", "copia.csv.gz",
    "resultado.parquet", "resultado.jsonl", "planilha.xlsx", "modelo.joblib",
    "copia.sqlite3-wal", "hmac.key", ".env", ".env.local",
])
def test_caminhos_restritos(filename):
    assert is_private_path(filename)


@pytest.mark.parametrize("filename", [
    "app/models/produto.py", "tests/conftest.py", "docs/BASELINE_FASE_0.md",
    "README.md", "requirements.txt", ".env.example",
])
def test_codigo_e_documentacao_permitidos(filename):
    assert not is_private_path(filename)


def test_gitignore_e_hook_bloqueiam_inclusao_forcada(tmp_path):
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    git("init", "--quiet")
    shutil.copy(root / ".gitignore", repo / ".gitignore")
    (repo / "scripts").mkdir()
    shutil.copy(root / "scripts/check_private_data.py", repo / "scripts/check_private_data.py")
    (repo / ".githooks").mkdir()
    hook = repo / ".githooks/pre-commit"
    shutil.copy(root / ".githooks/pre-commit", hook)
    hook.chmod(0o755)
    git("config", "core.hooksPath", ".githooks")
    git("config", "user.name", "Teste Local")
    git("config", "user.email", "teste@example.invalid")

    for filename in ["data/pedidos.csv", "data/processed/demanda.json", "data/reports/qualidade.html"]:
        target = repo / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("conteudo ficticio", encoding="utf-8")
        git("check-ignore", "--quiet", filename)

    git("add", ".")
    result = subprocess.run(
        [sys.executable, "scripts/check_private_data.py"], cwd=repo, capture_output=True,
    )
    assert result.returncode == 0
    # git add -f contorna o ignore, mas o hook ainda deve impedir o commit.
    git("add", "-f", "data/processed/demanda.json")
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Teste de bloqueio"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Commit bloqueado" in result.stderr
    assert "conteudo ficticio" not in result.stderr
