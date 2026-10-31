"""Consolidação por dia com fontes exclusivamente fictícias."""

import csv
from hashlib import sha256
import io
import json
import subprocess

import pytest

from app.services.preparacao_dados_service import OUTPUT_FIELDS, PreparationError
from scripts.consolidate_history import consolidate_history
from scripts.review_history import review_history


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("/data/\n")
    (root / "data/processed").mkdir(parents=True)
    (root / "data/reports").mkdir()

    def source(name, rows):
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(OUTPUT_FIELDS)
        for day, code, quantity in rows:
            writer.writerow((day, code, "Produto ficticio", "01", "Grupo ficticio", quantity, 1))
        content = buffer.getvalue()
        dataset = root / "data/processed" / (name + ".csv")
        dataset.write_text(content)
        report = root / "data/reports" / (name + ".json")
        report.write_text(json.dumps({
            "versao_pipeline": "3", "status": "aprovado", "politica_data": "dia_fonte",
            "erros": {}, "colunas_dataset": list(OUTPUT_FIELDS),
            "dataset": dataset.relative_to(root).as_posix(),
            "sha256_dataset": sha256(content.encode()).hexdigest(),
        }))
        return report

    base = source("base", [("2026-01-05", "001", "2.25"),
                           ("2026-01-06", "001", "10"), ("2026-01-06", "002", "7")])
    priority = source("prioridade", [("2026-01-06", "001", "3.5"), ("2026-01-07", "002", "4")])
    return root, base, priority


def test_prioridade_substitui_dia_inteiro_sem_somar_ou_manter_produto_antigo(sources):
    root, base, priority = sources
    originals = {path: path.read_bytes() for path in (root / "data").rglob("*") if path.is_file()}
    result = consolidate_history(root, base, priority)
    with result["dataset"].open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["data"], r["codigo_produto"], r["quantidade"]) for r in rows] == [
        ("2026-01-05", "001", "2.25"), ("2026-01-06", "001", "3.5"), ("2026-01-07", "002", "4")]
    report = json.loads(result["report"].read_text())
    assert report["dias_substituidos"] == ["2026-01-06"]
    assert report["linhas_base_substituidas"] == 2
    assert report["sha256_dataset"] == sha256(result["dataset"].read_bytes()).hexdigest()
    assert report["revisao_para_treinamento"] == "pendente"
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert result["dataset"].stat().st_mode & 0o777 == 0o600
    assert result["report"].stat().st_mode & 0o777 == 0o600
    assert result["dataset"].parent.stat().st_mode & 0o777 == 0o700


def test_reexecucao_produz_conteudo_identico_sem_sobrescrever(sources):
    root, base, priority = sources
    first = consolidate_history(root, base, priority)
    second = consolidate_history(root, base, priority)
    assert first["dataset"] != second["dataset"]
    assert first["dataset"].read_bytes() == second["dataset"].read_bytes()


@pytest.mark.parametrize("change", ["hash", "bloqueado", "symlink", "colunas", "metadados", "duplicata"])
def test_bloqueia_fontes_invalidas_sem_dataset_parcial(sources, change):
    root, base, priority = sources
    report = json.loads(priority.read_text())
    dataset = root / report["dataset"]
    if change == "hash":
        dataset.write_text(dataset.read_text() + "alterado")
    elif change == "bloqueado":
        report["status"] = "bloqueado"
    elif change == "colunas":
        report["colunas_dataset"] = ["data"]
    elif change == "symlink":
        link = dataset.with_name("link.csv")
        link.symlink_to(dataset)
        report["dataset"] = link.relative_to(root).as_posix()
    else:
        text = dataset.read_text()
        if change == "metadados":
            text = text.replace("Grupo ficticio", "Outro grupo ficticio")
        else:
            text += text.splitlines()[-1] + "\n"
        dataset.write_text(text)
        report["sha256_dataset"] = sha256(text.encode()).hexdigest()
    priority.write_text(json.dumps(report))
    with pytest.raises(PreparationError):
        consolidate_history(root, base, priority)
    assert not list((root / "data/processed").glob("consolidacao-*"))


def test_arquivo_publico_nao_e_aceito_como_relatorio(sources):
    root, base, priority = sources
    public = root / "public.json"
    public.write_bytes(base.read_bytes())
    with pytest.raises(PreparationError):
        consolidate_history(root, public, priority)


def test_revisao_consolidada_e_aceita_pelo_motor_sem_alterar_canonico(sources):
    from app.forecasting.service import load_review

    root, base, priority = sources
    consolidated = consolidate_history(root, base, priority)
    canonical = consolidated["dataset"].read_bytes()
    reviewed = review_history(root, consolidated["report"], cutoff="2026-01-06",
                              coverage_complete=True, exclude_weekends=True)
    rows, provenance = load_review(root, reviewed["report"], "2026-01-06")
    assert {row["data"] for row in rows} == {"2026-01-05", "2026-01-06"}
    assert provenance["coverage_complete"] is True
    assert consolidated["dataset"].read_bytes() == canonical
    assert reviewed["dataset"].stat().st_mode & 0o777 == 0o600
    assert reviewed["report"].stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("cutoff,complete,weekends", [
    ("2026-01-06", False, True), ("2026-1-6", True, True),
    ("2026-01-06", True, None), ("2020-01-01", True, True),
])
def test_revisao_nao_inventa_confirmacao_ou_recorte_vazio(sources, cutoff, complete, weekends):
    root, base, _ = sources
    with pytest.raises(PreparationError):
        review_history(root, base, cutoff=cutoff, coverage_complete=complete, exclude_weekends=weekends)
    assert not list((root / "data/processed").glob("revisao-decisoes-*"))
