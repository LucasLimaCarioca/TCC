"""Somente dados fictícios; nunca lê o CSV real durante a suíte."""

import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess

import pytest

from app.services.preparacao_dados_service import (
    OUTPUT_FIELDS, PreparationError, prepare_history,
)
from scripts import prepare_history as cli


HEADERS = [
    "pvNro", "cliNro", "proCodCli", "venNro", "gproNro", "cliNome", "venNome",
    "proNomeunificado", "gproNm", "pvEmis", "pvQtd", "pvPrcunt", "pvPrctot",
    "email", "telefone", "endereco", "coluna_extra",
]
PRIVATE_VALUES = [
    "CLIENTE_RESTRITO_TESTE", "VENDEDOR_RESTRITO_TESTE", "PESSOA_FICTICIA_TESTE",
    "VENDEDOR_FICTICIO_TESTE", "pessoa@example.invalid", "TELEFONE_FICTICIO_TESTE",
    "ENDERECO_FICTICIO_TESTE", "EXTRA_PESSOAL_TESTE",
]


def record(**changes):
    row = dict(zip(HEADERS, [
        "PEDIDO-TESTE-1", PRIVATE_VALUES[0], "00110", PRIVATE_VALUES[1], "00005",
        PRIVATE_VALUES[2], PRIVATE_VALUES[3], "Produto Ficticio", "Grupo Ficticio",
        "2025-01-02T00:00:00.000Z", "1.5", "2", "3",
        PRIVATE_VALUES[4], PRIVATE_VALUES[5], PRIVATE_VALUES[6], PRIVATE_VALUES[7],
    ]))
    row.update(changes)
    return row


@pytest.fixture
def private_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".gitignore").write_text("/data/\n")
    (root / "data").mkdir()
    return root


def write_source(root, rows, headers=HEADERS):
    source = root / "data/pedido.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return source


def run(root, **kwargs):
    result = prepare_history(root, date_policy="dia_fonte", **kwargs)
    report = json.loads(result["report"].read_text())
    dataset = []
    if result["dataset"]:
        with result["dataset"].open(newline="") as f:
            reader = csv.DictReader(f)
            assert tuple(reader.fieldnames) == OUTPUT_FIELDS
            dataset = list(reader)
    return result, report, dataset


def assert_no_personal_data(result):
    for key in ("dataset", "report"):
        if result[key]:
            text = result[key].read_text()
            for value in PRIVATE_VALUES + ["PEDIDO-TESTE-1", "PEDIDO-TESTE-2"]:
                assert value not in text
            for field in ["cliNro", "venNro", "cliNome", "venNome", "email", "telefone", "endereco", "coluna_extra"]:
                assert field not in text


def test_agrega_por_dia_produto_preserva_codigo_e_conta_pedidos_distintos(private_repo):
    source = write_source(private_repo, [
        record(), record(pvQtd="0.5", pvPrctot="1"),
        record(pvNro="PEDIDO-TESTE-2", pvQtd="2", pvPrctot="4"),
        record(pvEmis="2025-01-03T00:00:00Z", pvQtd="3", pvPrctot="6"),
        record(proCodCli="00220", proNomeunificado="Outro Produto Ficticio"),
    ])
    before = source.read_bytes()
    result, report, dataset = run(private_repo)
    assert result["status"] == "aprovado"
    assert dataset == [
        {"data": "2025-01-02", "codigo_produto": "00110", "codigo_grupo": "00005", "categoria": "Grupo Ficticio", "quantidade": "4.0", "numero_pedidos": "2"},
        {"data": "2025-01-02", "codigo_produto": "00220", "codigo_grupo": "00005", "categoria": "Grupo Ficticio", "quantidade": "1.5", "numero_pedidos": "1"},
        {"data": "2025-01-03", "codigo_produto": "00110", "codigo_grupo": "00005", "categoria": "Grupo Ficticio", "quantidade": "3", "numero_pedidos": "1"},
    ]
    assert report["versao_pipeline"] == "2"
    assert report["colunas_dataset"] == ["data", "codigo_produto", "codigo_grupo", "categoria", "quantidade", "numero_pedidos"]
    assert report["linhas_lidas"] == report["linhas_validas"] == 5
    assert report["numero_produtos"] == 2
    assert report["numero_pedidos"] == 2
    assert report["produtos_encontrados"] == ["00110", "00220"]
    assert report["intervalo_temporal"] == {"inicio": "2025-01-02", "fim": "2025-01-03"}
    assert report["sha256_fonte"] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    assert_no_personal_data(result)
    assert result["dataset"].stat().st_mode & 0o777 == 0o600
    assert result["report"].stat().st_mode & 0o777 == 0o600


def test_reexecucao_nao_acumula_demanda_nem_sobrescreve_arquivos(private_repo):
    write_source(private_repo, [record()])
    first, first_report, _ = run(private_repo)
    second, second_report, _ = run(private_repo)
    assert first["dataset"] != second["dataset"]
    assert first["dataset"].read_bytes() == second["dataset"].read_bytes()
    assert first_report["sha256_dataset"] == second_report["sha256_dataset"]


@pytest.mark.parametrize("policy,expected", [("dia_fonte", "2025-01-02"), ("America/Manaus", "2025-01-01")])
def test_politica_data_explicita(private_repo, policy, expected):
    write_source(private_repo, [record()])
    result = prepare_history(private_repo, date_policy=policy)
    with result["dataset"].open() as f:
        assert next(csv.DictReader(f))["data"] == expected


@pytest.mark.parametrize("field,value,error", [
    ("pvQtd", "invalido", "quantidade_invalida"),
    ("pvQtd", "NaN", "quantidade_invalida"),
    ("pvQtd", "Infinity", "quantidade_invalida"),
    ("pvQtd", "-2", "quantidade_negativa"),
    ("pvQtd", "1e1000", "quantidade_invalida"),
    ("pvEmis", "2025-02-30T00:00:00Z", "data_invalida_ou_sem_fuso"),
    ("pvEmis", "2025-01-02T00:00:00", "data_invalida_ou_sem_fuso"),
    ("pvPrcunt", "NaN", "valor_comercial_invalido"),
    ("pvPrctot", "-1", "valor_comercial_negativo"),
    ("proCodCli", "", "campo_obrigatorio_vazio"),
])
def test_linha_invalida_bloqueia_dataset_parcial(private_repo, field, value, error):
    write_source(private_repo, [record(), record(**{field: value})])
    result, report, dataset = run(private_repo)
    assert result["status"] == "bloqueado"
    assert result["dataset"] is None
    assert dataset == []
    assert report["linhas_invalidas"] == 1
    assert report["erros"][error] == 1
    assert not (private_repo / "data/processed").exists()
    assert_no_personal_data(result)


def test_duplicata_exata_exige_revisao_sem_descartar_silenciosamente(private_repo):
    write_source(private_repo, [record(), record()])
    result, report, _ = run(private_repo)
    assert result["status"] == "bloqueado"
    assert report["duplicatas_exatas"] == 1
    assert result["dataset"] is None


@pytest.mark.parametrize("field,value", [("proNomeunificado", "Nome Diferente Ficticio"), ("gproNro", "00999"), ("gproNm", "Categoria Diferente Ficticia")])
def test_inconsistencia_produto_bloqueia_liberacao(private_repo, field, value):
    write_source(private_repo, [record(), record(**{field: value})])
    result, report, _ = run(private_repo)
    assert result["status"] == "bloqueado"
    assert report["erros"]["metadados_produto_exigem_revisao"] == 1
    assert value not in result["report"].read_text()


def test_quantidade_zero_contabilizada_sem_inventar_dias(private_repo):
    write_source(private_repo, [record(pvQtd="0", pvPrctot="0")])
    result, report, dataset = run(private_repo)
    assert result["status"] == "aprovado"
    assert report["quantidades_zero"] == 1
    assert len(dataset) == 1
    assert dataset[0]["quantidade"] == "0"


def test_schema_errado_bloqueia_sem_copiar_campos_desconhecidos(private_repo):
    write_source(private_repo, [record()], headers=["pvNro", "cliNome", "coluna_extra"])
    result, report, _ = run(private_repo)
    assert result["status"] == "bloqueado"
    assert "proCodCli" in report["campos_obrigatorios_ausentes"]
    assert_no_personal_data(result)


@pytest.mark.parametrize("content", [
    "", ",".join(HEADERS) + "\n", ",".join(HEADERS + ["pvQtd"]) + "\n",
    ",".join(HEADERS) + '\n"aspas sem fechamento',
    ",".join(HEADERS) + "\na,b,c\n",
])
def test_csv_vazio_malformado_ou_cabecalho_duplicado(private_repo, content):
    (private_repo / "data/pedido.csv").write_text(content)
    result, report, _ = run(private_repo)
    assert result["status"] == "bloqueado"
    assert report["erros"]


def test_opcionais_ausentes_nao_introduzem_identificadores(private_repo):
    headers = ["pvNro", "proCodCli", "proNomeunificado", "pvEmis", "pvQtd"]
    write_source(private_repo, [record()], headers=headers)
    result, report, dataset = run(private_repo)
    assert result["status"] == "aprovado"
    assert report["valores_ausentes"]["pvPrcunt"] == 1
    assert set(dataset[0]) == set(OUTPUT_FIELDS)
    assert dataset[0]["codigo_grupo"] == ""
    assert dataset[0]["categoria"] == ""


def test_fonte_fora_de_data_recusada(private_repo):
    with pytest.raises(PreparationError, match="data/"):
        prepare_history(private_repo, "arquivo.csv", date_policy="dia_fonte")


def test_fonte_nao_ignorada_recusada(private_repo):
    write_source(private_repo, [record()])
    (private_repo / ".gitignore").write_text("")
    with pytest.raises(PreparationError, match="ignorados"):
        run(private_repo)


def test_fonte_ja_rastreada_recusada_mesmo_apos_ignore(private_repo):
    write_source(private_repo, [record()])
    (private_repo / ".gitignore").write_text("")
    subprocess.run(["git", "-C", str(private_repo), "add", "data/pedido.csv"], check=True)
    (private_repo / ".gitignore").write_text("/data/\n")
    with pytest.raises(PreparationError, match="índice Git"):
        run(private_repo)


def test_link_na_fonte_recusado(private_repo, tmp_path):
    outside = tmp_path / "fonte_ficticia.csv"
    outside.write_text("conteudo ficticio")
    (private_repo / "data/pedido.csv").symlink_to(outside)
    with pytest.raises(PreparationError, match="simbólicos"):
        run(private_repo)


def test_link_na_saida_recusado(private_repo, tmp_path):
    write_source(private_repo, [record()])
    outside = tmp_path / "destino"
    outside.mkdir()
    (private_repo / "data/processed").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PreparationError, match="simbólicos"):
        run(private_repo)
    assert list(outside.iterdir()) == []


def test_cli_nao_expoe_valores_invalidos(private_repo, monkeypatch, capsys):
    write_source(private_repo, [record(pvQtd=PRIVATE_VALUES[2])])
    monkeypatch.setattr(cli, "__file__", str(private_repo / "scripts/prepare_history.py"))
    assert cli.main(["--date-policy", "dia_fonte"]) == 1
    captured = capsys.readouterr()
    assert "Dataset bloqueado" in captured.out
    assert not captured.err
    assert all(value not in captured.out for value in PRIVATE_VALUES)


def test_cli_sucesso_e_politica_obrigatoria(private_repo, monkeypatch, capsys):
    write_source(private_repo, [record()])
    monkeypatch.setattr(cli, "__file__", str(private_repo / "scripts/prepare_history.py"))
    with pytest.raises(SystemExit) as failure:
        cli.main([])
    assert failure.value.code == 2
    capsys.readouterr()
    assert cli.main(["--date-policy", "dia_fonte"]) == 0
    output = capsys.readouterr().out
    assert "Preparação concluída" in output
    assert all(value not in output for value in PRIVATE_VALUES)


def test_exporta_grupos_de_produtos_distintos_sem_misturar(private_repo):
    write_source(private_repo, [
        record(),
        record(proCodCli="00220", proNomeunificado="Outro Produto Ficticio",
               gproNro="00009", gproNm="Categoria Ficticia B"),
    ])
    result, _, dataset = run(private_repo)
    assert result["status"] == "aprovado"
    assert [(r["codigo_produto"], r["codigo_grupo"], r["categoria"]) for r in dataset] == [
        ("00110", "00005", "Grupo Ficticio"), ("00220", "00009", "Categoria Ficticia B"),
    ]
    assert_no_personal_data(result)


def test_mesmo_codigo_grupo_com_nomes_diferentes_bloqueia(private_repo):
    write_source(private_repo, [
        record(),
        record(proCodCli="00220", proNomeunificado="Outro Produto Ficticio",
               gproNm="Categoria Ficticia B"),
    ])
    result, report, _ = run(private_repo)
    assert result["status"] == "bloqueado"
    assert result["dataset"] is None
    assert report["inconsistencias"]["grupos_com_varios_nomes"] == 1
    assert "Categoria Ficticia B" not in result["report"].read_text()


def test_metadados_ausentes_nao_sao_inferidos_de_outro_dia(private_repo):
    write_source(private_repo, [
        record(gproNro="", gproNm=""),
        record(pvEmis="2025-01-03T00:00:00Z"),
    ])
    result, report, dataset = run(private_repo)
    assert result["status"] == "aprovado"
    assert dataset[0]["codigo_grupo"] == dataset[0]["categoria"] == ""
    assert dataset[1]["codigo_grupo"] == "00005"
    assert dataset[1]["categoria"] == "Grupo Ficticio"
    assert report["valores_ausentes"]["gproNro"] == 1
    assert report["valores_ausentes"]["gproNm"] == 1


def test_mesmo_dia_agrega_com_metadados_disponiveis_e_espacos_normalizados(private_repo):
    write_source(private_repo, [
        record(gproNro="", gproNm=""),
        record(pvNro="PEDIDO-TESTE-2", gproNro=" 00005 ", gproNm=" Grupo Ficticio "),
    ])
    result, _, dataset = run(private_repo)
    assert result["status"] == "aprovado"
    assert len(dataset) == 1
    assert dataset[0]["codigo_grupo"] == "00005"
    assert dataset[0]["categoria"] == "Grupo Ficticio"
    assert dataset[0]["quantidade"] == "3.0"
    assert dataset[0]["numero_pedidos"] == "2"
