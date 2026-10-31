"""Coluna comercial adicional em dados fictícios, sem mudar observações gravadas."""

import csv

import pytest

from app.database import db
from app.models.produto import Produto
from app.models.venda_historica import VendaHistorica
from app.services import importacao_service as service
from app.services.preparacao_dados_service import OUTPUT_FIELDS, LEGACY_OUTPUT_FIELDS
from test_importacao_historico import project, prepared, row, run, count


def named(root, rows, *, version="3", fields=OUTPUT_FIELDS):
    return prepared(root, rows, fields=fields, changes={
        "versao_pipeline": version, "colunas_dataset": list(fields)})


def test_nome_completo_nao_muda_hash_da_observacao_nem_duplica_importacao_legada(app, project):
    legacy, _ = prepared(project)
    assert run(app, project, legacy)[0]["status"] == "importado"
    with app.app_context():
        original = service._stored_signature(VendaHistorica.query.one())
    full_name = 'Sorvete Fictício, Chocolate "Extra" ' + "descrição extensa " * 300
    full_name = full_name.strip()
    source, dataset = named(project, [row(nome_completo=full_name)])
    before = dataset.read_bytes()
    result, report = run(app, project, source)
    assert result["status"] == "importado"
    assert report["versao_preparacao"] == "3" and report["colunas_dataset"] == list(OUTPUT_FIELDS)
    assert report["linhas_ja_importadas"] == 1 and report["linhas_inseridas"] == 0
    assert dataset.read_bytes() == before
    with dataset.open(newline="") as file:
        assert next(csv.DictReader(file))["nome_completo"] == full_name
    assert full_name not in result["report"].read_text()
    with app.app_context():
        assert VendaHistorica.query.count() == 1 and Produto.query.count() == 0
        assert service._stored_signature(VendaHistorica.query.one()) == original
        assert set(service.serie_diaria("00110")[0]) == set(LEGACY_OUTPUT_FIELDS)


@pytest.mark.parametrize("day", ["2025-01-02", "2025-01-03"])
def test_nomes_divergentes_do_mesmo_codigo_bloqueiam_lote(app, project, day):
    source, _ = named(project, [row(nome_completo="Produto fictício A"),
                               row(data=day, nome_completo="Produto fictício B")])
    result, report = run(app, project, source)
    assert result["status"] == "bloqueado" and report["erros"]["metadados_inconsistentes"]
    assert count(app) == 0


def test_nome_vazio_na_versao3_nao_e_importado(app, project):
    source, _ = named(project, [row(nome_completo=" ")])
    result, report = run(app, project, source)
    assert result["status"] == "bloqueado" and report["erros"]["nome_completo_invalido"] == 1
    assert count(app) == 0


@pytest.mark.parametrize("version,fields", [("2", OUTPUT_FIELDS), ("3", LEGACY_OUTPUT_FIELDS)])
def test_versao_e_schema_devem_corresponder(app, project, version, fields):
    source, _ = named(project, [row(nome_completo="Produto fictício")], version=version, fields=fields)
    with app.app_context(), pytest.raises(service.ImportacaoError):
        service.importar_historico(project, source)
    assert count(app) == 0
