"""Leitura dos dois schemas diários usando apenas produtos e dados fictícios."""

from copy import deepcopy
import csv
from datetime import date, timedelta
from hashlib import sha256
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from app.forecasting import service
from app.forecasting.dataset import build_series
from app.forecasting.errors import ForecastError
from app.services.preparacao_dados_service import DATASET_SCHEMAS
from app.services.previsao_painel_service import _update


@pytest.fixture
def private_project(tmp_path):
    root = tmp_path / "projeto ficticio com nomes"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("/data/\n")
    for directory in ("processed", "reports"):
        (root / "data" / directory).mkdir(parents=True)
    return root


def row(**changes):
    return {"data": "2026-01-05", "codigo_produto": "000731",
        "nome_completo": "Produto Fictício Alpha — embalagem de 10L",
        "codigo_grupo": "000005", "categoria": "Categoria fictícia",
        "quantidade": "1.25", "numero_pedidos": "1", **changes}


def write_chain(root, *, fields=None, version="3"):
    fields = tuple(fields or DATASET_SCHEMAS[version])
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(fields)
    for offset in range(5):
        for code, name in (("000731", "Produto Fictício Alpha — embalagem de 10L"),
                           ("009842", "Produto Fictício Beta — embalagem de 5L")):
            values = row(data=(date(2026, 1, 5) + timedelta(days=offset)).isoformat(),
                         codigo_produto=code, nome_completo=name)
            writer.writerow([values[field] for field in fields])
    content = output.getvalue()
    digest = sha256(content.encode()).hexdigest()
    for name in ("canonico.csv", "revisado.csv"):
        (root / "data/processed" / name).write_text(content)
    preparation = {"status": "aprovado", "versao_pipeline": version, "erros": {},
        "politica_data": "dia_fonte", "colunas_dataset": list(fields),
        "dataset": "data/processed/canonico.csv", "sha256_dataset": digest}
    preparation_path = root / "data/reports/preparacao-ficticia.json"
    preparation_path.write_text(json.dumps(preparation))
    review = {"status": "revisado_com_recorte", "politica_data": "dia_fonte",
        "colunas": list(fields), "contexto": {"exportacao_completa": True},
        "data_referencia": "2026-01-09", "relatorio_preparacao": "data/reports/preparacao-ficticia.json",
        "dataset_canonico": preparation["dataset"], "sha256_canonico": digest,
        "dataset_revisado": "data/processed/revisado.csv", "sha256_revisado": digest}
    path = root / "data/reports/revisao-ficticia.json"
    path.write_text(json.dumps(review))
    return path


class Calendar:
    def is_open(self, day):
        return day.weekday() < 5


def test_serie_preserva_nome_integral_sem_mudar_quantidades_ou_origem():
    name = "Produto fictício especial\n" + "Nome completo extenso " * 300
    name = name.strip()
    rows = [row(nome_completo=name), row(data="2026-01-06", nome_completo=name, quantidade="2.75")]
    before = deepcopy(rows)
    result = build_series(rows, Calendar(), date(2026, 1, 6), coverage_complete=True)
    legacy = [{field: value for field, value in item.items() if field != "nome_completo"} for item in rows]
    original = build_series(legacy, Calendar(), date(2026, 1, 6), coverage_complete=True)
    assert rows == before
    assert result["series"]["000731"].equals(original["series"]["000731"])
    assert result["report"]["products"]["000731"]["nome_completo"] == name
    assert "nome_completo" not in original["report"]["products"]["000731"]


@pytest.mark.parametrize("name", [None, "", "  ", " Nome com borda ", 42])
def test_nome_completo_invalido_nao_e_copiado_para_erro(name):
    with pytest.raises(ForecastError) as error:
        build_series([row(nome_completo=name)], Calendar(), date(2026, 1, 5), coverage_complete=True)
    assert "Nome com borda" not in str(error.value)


def test_nome_divergente_por_codigo_e_schema_misto_sao_rejeitados():
    with pytest.raises(ForecastError, match="inconsistentes"):
        build_series([row(), row(data="2026-01-06", nome_completo="Outro nome fictício")],
                     Calendar(), date(2026, 1, 6), coverage_complete=True)
    legacy = {field: value for field, value in row(data="2026-01-06").items() if field != "nome_completo"}
    with pytest.raises(ForecastError, match="mesmo schema"):
        build_series([row(), legacy], Calendar(), date(2026, 1, 6), coverage_complete=True)


@pytest.mark.parametrize("version", ["2", "3"])
def test_revisoes_legadas_e_com_nome_continuam_legiveis(private_project, version):
    path = write_chain(private_project, version=version)
    rows, provenance = service.load_review(private_project, path, "2026-01-09")
    assert len(rows) == 10 and rows[0]["codigo_produto"] == "000731"
    assert set(rows[0]) == set(DATASET_SCHEMAS[version])
    assert ("nome_completo" in rows[0]) == (version == "3")
    assert provenance["date_policy"] == "dia_fonte"


@pytest.mark.parametrize("version,fields", [("2", DATASET_SCHEMAS["3"]),
                                            ("3", DATASET_SCHEMAS["2"])])
def test_versao_colunas_e_revisao_precisam_corresponder(private_project, version, fields):
    path = write_chain(private_project, version=version, fields=fields)
    with pytest.raises(ForecastError, match="incompatível"):
        service.load_review(private_project, path, "2026-01-09")


def test_cabecalho_divergente_nao_e_aceito_mesmo_com_hash_atualizado(private_project):
    path = write_chain(private_project)
    dataset = private_project / "data/processed/revisado.csv"
    lines = dataset.read_text().splitlines()
    lines[0] = ",".join(DATASET_SCHEMAS["2"])
    dataset.write_text("\n".join(lines) + "\n")
    review = json.loads(path.read_text())
    review["sha256_revisado"] = sha256(dataset.read_bytes()).hexdigest()
    path.write_text(json.dumps(review))
    with pytest.raises(ForecastError, match="colunas diárias"):
        service.load_review(private_project, path, "2026-01-09")


def test_novo_relatorio_guarda_nome_e_previsao_permanece_igual_ao_legado(private_project):
    path = write_chain(private_project, version="2")
    kwargs = {"cutoff": "2026-01-09", "start": "2026-01-05", "horizon": 7}
    legacy_paths = service.run_forecast(private_project, path, **kwargs)
    before = {file: file.read_bytes() for file in legacy_paths.values()}
    path = write_chain(private_project, version="3")
    paths = service.run_forecast(private_project, path, **kwargs)
    report = json.loads(paths["report"].read_text())
    assert report["dataset"]["products"]["000731"]["nome_completo"] == row()["nome_completo"]
    assert json.loads(paths["forecast"].read_text())["forecast"] == json.loads(legacy_paths["forecast"].read_text())["forecast"]
    assert json.loads(paths["report"].read_text())["evaluations"] == json.loads(legacy_paths["report"].read_text())["evaluations"]
    assert {file: file.read_bytes() for file in legacy_paths.values()} == before


@pytest.mark.parametrize("version", ["2", "3"])
def test_painel_habilita_recalculo_com_revisao_de_ambos_schemas(private_project, version):
    path = write_chain(private_project, version=version)
    adapter = SimpleNamespace(_config=lambda key: path, _file=lambda candidate, directory: path,
                              _json=lambda content: json.loads(content))
    result = _update(adapter, {"horizon_days": 7, "cutoff": "2026-01-09"}, date(2026, 1, 10))
    assert result["disponivel"] is True and result["data_corte_maxima"] == "2026-01-09"
