"""Histórico inteiramente fictício em repositórios e SQLite temporários."""

from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
import os
import subprocess
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.database import db
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.models.venda_historica import VendaHistorica
from app.services import importacao_service as service
from app.services.preparacao_dados_service import OUTPUT_FIELDS, prepare_history
from scripts import import_history as cli


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".gitignore").write_text("/data/\n")
    return root


def row(**changes):
    result = {
        "data": "2025-01-02", "codigo_produto": "00110", "codigo_grupo": "00005",
        "categoria": "Grupo Fictício", "quantidade": "1.25", "numero_pedidos": "1",
    }
    result.update(changes)
    return result


def prepared(root, rows=None, *, fields=OUTPUT_FIELDS, changes=None, content=None):
    identifier = "preparacao-" + uuid4().hex
    directory = root / "data/processed" / identifier
    directory.mkdir(parents=True)
    if content is None:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(fields)
        for values in rows if rows is not None else [row()]:
            writer.writerow([values.get(field, "DADO_FICTICIO_NAO_PUBLICAR") for field in fields])
        content = output.getvalue().encode("utf-8")
    dataset = directory / "demanda_diaria.csv"
    dataset.write_bytes(content)
    report = {
        "status": "aprovado", "versao_pipeline": "2", "politica_data": "dia_fonte",
        "sha256_fonte": hashlib.sha256(b"fonte ficticia independente").hexdigest(),
        "sha256_dataset": hashlib.sha256(content).hexdigest(),
        "dataset": dataset.relative_to(root).as_posix(), "erros": {},
        "colunas_dataset": list(OUTPUT_FIELDS),
    }
    report.update(changes or {})
    reports = root / "data/reports"
    reports.mkdir(exist_ok=True)
    path = reports / (identifier + ".json")
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, dataset


def run(app, project, preparation, **options):
    with app.app_context():
        result = service.importar_historico(project, preparation, **options)
    return result, json.loads(result["report"].read_text())


def count(app):
    with app.app_context():
        return VendaHistorica.query.count()


def test_importa_preparado_e_reproduz_serie_sem_alterar_catalogo_ou_estoque(app, catalogo, project):
    with app.app_context():
        product = db.session.get(Produto, catalogo["caixa de 10L - chocolate"])
        product.codigo_externo = "00110"
        db.session.commit()
        initial = [(item.id, item.nome, item.categoria, item.quantidade_disponivel) for item in Produto.query.all()]
    preparation, dataset = prepared(project, [row(data="2025-01-03", quantidade="2"),
        row(data="2025-01-01", quantidade="1.2", numero_pedidos="2"), row(codigo_produto="00220")])
    before = dataset.read_bytes(), preparation.read_bytes()
    directory_mode = preparation.parent.stat().st_mode & 0o777
    result, report = run(app, project, preparation)
    assert result["status"] == "importado"
    assert report["linhas_inseridas"] == report["linhas_novas"] == 3
    assert report["linhas_lidas"] == report["linhas_validas"] == 3
    assert report["linhas_rejeitadas"] == 0
    assert report["numero_produtos"] == 2
    assert report["produtos_sem_mapeamento"] == ["00220"]
    assert report["produtos_mapeados"] == 1
    assert report["intervalo_temporal"] == {"inicio": "2025-01-01", "fim": "2025-01-03"}
    assert report["perfil_por_produto"]["00110"]["dias_sem_observacao_no_intervalo"] == 1
    assert report["perfil_por_produto"]["00110"]["quantidade_total"] == "3.2"
    assert result["report"].is_relative_to(project / "data/reports")
    assert result["report"].stat().st_mode & 0o777 == 0o600
    assert result["report"].parent.stat().st_mode & 0o777 == directory_mode
    assert (dataset.read_bytes(), preparation.read_bytes()) == before
    with app.app_context():
        assert [(item.id, item.nome, item.categoria, item.quantidade_disponivel) for item in Produto.query.all()] == initial
        assert Venda.query.count() == MovimentacaoEstoque.query.count() == 0
        series = service.serie_diaria("00110")
        assert [entry["data"] for entry in series] == ["2025-01-01", "2025-01-03"]
        assert [entry["quantidade"] for entry in series] == ["1.2", "2"]
        assert series[0]["codigo_grupo"] == "00005"
        assert series[0]["categoria"] == "Grupo Fictício"
        assert series[0]["numero_pedidos"] == 2
        assert service.serie_diaria("inexistente") == []
        assert service.serie_diaria("00110", inicio="2025-01-02", fim="2025-01-03") == [series[1]]
        assert all(record.importado_em and len(record.source_hash) == 64 and
                   record.dataset_hash == report["sha256_dataset"] for record in VendaHistorica.query.all())


def test_reimportacao_e_dataset_reordenado_equivalente_preservam_origem(app, project):
    preparation, _ = prepared(project, [row(), row(codigo_produto="00220")])
    run(app, project, preparation)
    with app.app_context():
        before = [(record.id, record.source_hash, record.dataset_hash, record.importado_em) for record in VendaHistorica.query.order_by(VendaHistorica.id)]
    _, repeat = run(app, project, preparation)
    other, _ = prepared(project, [row(codigo_produto="00220", quantidade="1.250000000"), row(quantidade="1.250")])
    _, equivalent = run(app, project, other)
    assert repeat["linhas_ja_importadas"] == equivalent["linhas_ja_importadas"] == 2
    assert repeat["linhas_inseridas"] == equivalent["linhas_inseridas"] == 0
    with app.app_context():
        after = [(record.id, record.source_hash, record.dataset_hash, record.importado_em) for record in VendaHistorica.query.order_by(VendaHistorica.id)]
    assert after == before


def test_duplicatas_equivalentes_no_dataset_contam_uma_observacao(app, project):
    preparation, _ = prepared(project, [row(), row(quantidade="1.250")])
    _, report = run(app, project, preparation)
    assert report["linhas_lidas"] == 2
    assert report["duplicatas_no_dataset"] == 1
    assert report["observacoes_unicas"] == report["linhas_inseridas"] == count(app) == 1


@pytest.mark.parametrize("changes", [{"quantidade": "2"}, {"numero_pedidos": "3"},
                                    {"categoria": "Outro Grupo Fictício"}, {"codigo_grupo": "00006"}])
def test_sobreposicao_divergente_bloqueia_todas_as_linhas_novas(app, project, changes):
    first, _ = prepared(project)
    run(app, project, first)
    with app.app_context():
        before = service.serie_diaria("00110")
    second, _ = prepared(project, [row(**changes), row(codigo_produto="00220", codigo_grupo="", categoria="")])
    result, report = run(app, project, second)
    assert result["status"] == "bloqueado"
    assert report["conflitos_com_banco"] == 1
    assert report["linhas_novas"] == 1
    assert report["linhas_inseridas"] == 0
    assert report["erros"] == {"observacoes_divergentes_no_banco": 1}
    with app.app_context():
        assert service.serie_diaria("00110") == before
        assert VendaHistorica.query.count() == 1


def test_sobreposicao_identica_importa_so_dias_novos(app, project):
    first, _ = prepared(project)
    run(app, project, first)
    second, _ = prepared(project, [row(), row(data="2025-01-03")])
    _, report = run(app, project, second)
    assert report["linhas_ja_importadas"] == report["linhas_inseridas"] == 1
    assert count(app) == 2


@pytest.mark.parametrize("changes,error", [
    ({"data": "2025-02-30"}, "data_invalida"), ({"data": "20250102"}, "data_invalida"),
    ({"data": "2025-01-02T00:00:00Z"}, "data_invalida"),
    ({"quantidade": "NaN"}, "quantidade_invalida"), ({"quantidade": "Infinity"}, "quantidade_invalida"),
    ({"quantidade": "-1"}, "quantidade_invalida"), ({"quantidade": "1,2"}, "quantidade_invalida"),
    ({"quantidade": "0.0000000001"}, "quantidade_invalida"), ({"quantidade": "1e16"}, "quantidade_invalida"),
    ({"numero_pedidos": "1.5"}, "numero_pedidos_invalido"), ({"numero_pedidos": "-1"}, "numero_pedidos_invalido"),
    ({"numero_pedidos": str(2**63)}, "numero_pedidos_invalido"),
    ({"codigo_produto": ""}, "metadado_invalido"), ({"codigo_produto": "x" * 65}, "metadado_invalido"),
    ({"categoria": "x" * 101}, "metadado_invalido"), ({"codigo_grupo": "x" * 65}, "metadado_invalido"),
])
def test_erros_de_formato_geram_relatorio_sem_importacao_parcial(app, project, changes, error):
    preparation, _ = prepared(project, [row(codigo_produto="00220"), row(**changes)])
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["linhas_lidas"] == 2
    assert report["linhas_validas"] == report["linhas_rejeitadas"] == 1
    assert report["erros"][error] == 1
    assert count(app) == 0


def test_quantidade_zero_e_metadados_opcionais_sem_inferencia(app, project):
    preparation, _ = prepared(project, [row(quantidade="0", numero_pedidos="0", codigo_grupo="", categoria="")])
    _, report = run(app, project, preparation)
    assert report["perfil_por_produto"]["00110"]["dias_observados_com_quantidade_zero"] == 1
    with app.app_context():
        assert service.serie_diaria("00110")[0] == {
            "data": "2025-01-02", "codigo_produto": "00110", "codigo_grupo": "", "categoria": "",
            "quantidade": "0", "numero_pedidos": 0,
        }


@pytest.mark.parametrize("fields", [OUTPUT_FIELDS[:-1], (*OUTPUT_FIELDS, "cliNome"),
                                    (*OUTPUT_FIELDS, "pvPrcunt"), (*OUTPUT_FIELDS, "categoria")])
def test_schema_fechado_rejeita_colunas_extras_ou_ausentes(app, project, fields):
    preparation, _ = prepared(project, fields=fields)
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["erros"] == {"schema_incompativel": 1}
    assert "DADO_FICTICIO_NAO_PUBLICAR" not in result["report"].read_text()
    assert count(app) == 0


@pytest.mark.parametrize("changes", [{"status": "bloqueado"}, {"versao_pipeline": "1"},
    {"politica_data": "America/Manaus"}, {"sha256_fonte": "invalido"}, {"sha256_dataset": "invalido"},
    {"erros": {"qualidade": 1}}, {"colunas_dataset": ["cliNome"]}])
def test_relatorio_nao_aprovado_ou_incompativel_e_rejeitado(app, project, changes):
    preparation, _ = prepared(project, changes=changes)
    with app.app_context(), pytest.raises(service.ImportacaoError, match="relatório aprovado"):
        service.importar_historico(project, preparation)
    assert count(app) == 0


def test_csv_modificado_depois_da_preparacao_bloqueia_hash(app, project):
    preparation, dataset = prepared(project)
    dataset.write_bytes(dataset.read_bytes() + b"DADO_FICTICIO_NAO_PUBLICAR\n")
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["erros"] == {"hash_dataset_divergente": 1}
    assert "DADO_FICTICIO_NAO_PUBLICAR" not in result["report"].read_text()
    assert count(app) == 0


@pytest.mark.parametrize("case", ["vazio", "encoding", "estrutura", "aspas"])
def test_csv_inutilizavel_sem_registros_parciais(app, project, case):
    header = ",".join(OUTPUT_FIELDS) + "\n"
    content = {"vazio": header.encode(), "encoding": b"\xff", "estrutura": (header + "a,b\n").encode(),
               "aspas": (header + '\"aspas sem fechamento').encode()}[case]
    preparation, _ = prepared(project, content=content)
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["erros"]
    assert count(app) == 0


def test_conflitos_entre_linhas_do_dataset_sao_bloqueados(app, project):
    preparation, _ = prepared(project, [row(), row(quantidade="2")])
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["erros"] == {"observacoes_divergentes_no_dataset": 1}
    assert count(app) == 0


def test_metadados_contraditorios_entre_dias_bloqueiam_dataset(app, project):
    preparation, _ = prepared(project, [row(), row(data="2025-01-03", categoria="Outra Categoria Fictícia")])
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["erros"] == {"metadados_inconsistentes": 1}
    assert count(app) == 0


def test_mesmo_grupo_com_nomes_diferentes_entre_produtos_bloqueia_dataset(app, project):
    preparation, _ = prepared(project, [row(), row(codigo_produto="00220", categoria="Outra Categoria Fictícia")])
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["erros"] == {"metadados_inconsistentes": 1}
    assert count(app) == 0


def test_validar_apenas_e_serie_diaria_sao_somente_leitura(app, project):
    preparation, _ = prepared(project)
    statements = []
    with app.app_context():
        engine = db.engine
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(engine, "before_cursor_execute", capture)
        try:
            result = service.importar_historico(project, preparation, validar_apenas=True)
            assert service.serie_diaria("00110") == []
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    assert result["status"] == "validado"
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert count(app) == 0


@pytest.mark.parametrize("failure", ["relatorio", "commit"])
def test_falhas_de_gravacao_revertem_historico_e_nao_expoem_dados(app, project, monkeypatch, failure):
    preparation, _ = prepared(project, [row(), row(codigo_produto="00220")])
    def fail(*args, **kwargs):
        raise RuntimeError("DADO_FICTICIO_NAO_PUBLICAR")
    monkeypatch.setattr(service, "_write_private", fail) if failure == "relatorio" else monkeypatch.setattr(Session, "commit", fail)
    with app.app_context(), pytest.raises(service.ImportacaoError) as caught:
        service.importar_historico(project, preparation)
    assert "DADO_FICTICIO_NAO_PUBLICAR" not in str(caught.value)
    assert count(app) == 0
    assert not list((project / "data/reports").glob("importacao-*.json"))


def test_perda_de_precisao_no_sqlite_bloqueia_antes_do_commit(app, project):
    preparation, _ = prepared(project, [row(quantidade="999999999999999.123456789"), row(codigo_produto="00220")])
    result, report = run(app, project, preparation)
    assert result["status"] == "bloqueado"
    assert report["erros"] == {"precisao_nao_preservada_no_sqlite": 1}
    assert report["linhas_inseridas"] == count(app) == 0


def test_duas_importacoes_concorrentes_nao_duplicam_historico(app, project):
    preparation, _ = prepared(project, [row(), row(data="2025-01-03")])
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: run(app, project, preparation)[1], range(2)))
    assert sorted(report["linhas_inseridas"] for report in reports) == [0, 2]
    assert count(app) == 2


@pytest.mark.parametrize("case", ["fora", "nao_ignorado", "rastreado", "symlink", "hardlink", "bruto"])
def test_protecao_de_caminhos_e_exigida_antes_da_importacao(app, project, tmp_path, case):
    preparation, dataset = prepared(project)
    if case == "fora":
        outside = tmp_path / "relatorio.json"
        outside.write_bytes(preparation.read_bytes())
        preparation = outside
    elif case == "nao_ignorado":
        (project / ".gitignore").write_text("")
    elif case == "rastreado":
        (project / ".gitignore").write_text("")
        subprocess.run(["git", "-C", str(project), "add", preparation.relative_to(project).as_posix()], check=True)
        (project / ".gitignore").write_text("/data/\n")
    elif case == "symlink":
        target = tmp_path / "dataset_ficticio.csv"
        target.write_bytes(dataset.read_bytes())
        dataset.unlink()
        dataset.symlink_to(target)
    elif case == "hardlink":
        os.link(dataset, tmp_path / "vinculo_ficticio.csv")
    else:
        target = project / "data/pedido.csv"
        target.write_bytes(dataset.read_bytes())
        report = json.loads(preparation.read_text())
        report["dataset"] = "data/pedido.csv"
        preparation.write_text(json.dumps(report))
    with app.app_context(), pytest.raises(service.ImportacaoError):
        service.importar_historico(project, preparation)
    assert count(app) == 0


def test_preparacao_real_do_pipeline_com_fonte_ficticia_preserva_dia_e_remove_pessoas(app, project):
    source = project / "data/pedido.csv"
    source.parent.mkdir()
    source.write_text("pvNro,proCodCli,proNomeunificado,pvEmis,pvQtd,gproNro,gproNm,cliNome\n"
                      "PEDIDO-FICTICIO,00110,Produto Fictício,2025-01-02T00:00:00Z,1.25,00005,Grupo Fictício,PESSOA_FICTICIA_REMOVER\n", encoding="utf-8")
    before = source.read_bytes()
    prep = prepare_history(project, date_policy="dia_fonte")
    result, _ = run(app, project, prep["report"])
    assert result["status"] == "importado"
    assert "PESSOA_FICTICIA_REMOVER" not in result["report"].read_text()
    assert source.read_bytes() == before
    with app.app_context():
        assert VendaHistorica.query.one().data == date(2025, 1, 2)
        assert VendaHistorica.query.one().quantidade == Decimal("1.25")


@pytest.mark.parametrize("validacao", [False, True])
def test_cli_informa_status_e_so_caminhos_gerados(app, project, monkeypatch, capsys, validacao):
    preparation, _ = prepared(project)
    monkeypatch.setattr(cli, "__file__", str(project / "scripts/import_history.py"))
    monkeypatch.setattr(cli, "create_app", lambda: app)
    args = ["--report", str(preparation)] + (["--validar-apenas"] if validacao else [])
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    assert "Relatório privado: data/reports/importacao-" in captured.out
    assert "00110" not in captured.out
    assert "Grupo Fictício" not in captured.out
    assert captured.err == ""
    assert count(app) == (0 if validacao else 1)


def test_cli_falhas_nao_expoem_dados_e_retornam_codigos_distintos(app, project, monkeypatch, capsys):
    preparation, _ = prepared(project, [row(quantidade="DADO_FICTICIO_NAO_PUBLICAR")])
    monkeypatch.setattr(cli, "__file__", str(project / "scripts/import_history.py"))
    monkeypatch.setattr(cli, "create_app", lambda: app)
    assert cli.main(["--report", str(preparation)]) == 1
    assert "DADO_FICTICIO_NAO_PUBLICAR" not in capsys.readouterr().out
    assert cli.main(["--report", "data/reports/inexistente.json"]) == 2
    assert "Traceback" not in capsys.readouterr().err
    assert count(app) == 0


def test_cli_falha_de_configuracao_nao_expoe_excecao(monkeypatch, capsys):
    def fail():
        raise RuntimeError("DADO_FICTICIO_NAO_PUBLICAR")
    monkeypatch.setattr(cli, "create_app", fail)
    assert cli.main(["--report", "data/reports/ficticio.json"]) == 2
    assert "DADO_FICTICIO_NAO_PUBLICAR" not in capsys.readouterr().err


@pytest.mark.parametrize("values", [{"codigo_produto": 110}, {"codigo_produto": ""},
    {"codigo_produto": "00110", "inicio": "2025-01-03", "fim": "2025-01-01"},
    {"codigo_produto": "00110", "inicio": "20250102"}, {"codigo_produto": "00110", "fim": 2}])
def test_consulta_serie_valida_codigo_e_intervalo(app, values):
    with app.app_context(), pytest.raises(service.ImportacaoError):
        service.serie_diaria(**values)
