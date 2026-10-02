"""Migrações verificadas em SQLite temporário, somente com dados fictícios."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import runpy
import sqlite3
import subprocess

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app.app import create_app
from app.database import db
from app.migrations import MigrationError, _config, upgrade_database
from app.models.produto import Produto
from app.services.venda_service import registrar_venda
from app.services import estoque_service


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def migration_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".gitignore").write_text("/data/\n")
    path = root / "banco.db"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
    yield root, path, app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def create_legacy(app):
    spec = spec_from_file_location("legacy_revision", ROOT / "migrations/versions/0001_baseline.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    with app.app_context(), db.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        connection.exec_driver_sql(
            "INSERT INTO produtos VALUES (7, 'Produto Ficticio', 'caixa de 10L', 'chocolate', 10, 'Descricao ficticia', 20, 1)"
        )
        connection.exec_driver_sql("INSERT INTO clientes VALUES (8, 'Cliente Ficticio', '000000000')")
        connection.exec_driver_sql("INSERT INTO vendas VALUES (9, 'Cliente Ficticio', 7, 2, 20, '2025-01-01 12:00:00')")
        connection.exec_driver_sql("INSERT INTO historico_conversas VALUES (10, 'Cliente Ficticio', 'oi', 'ola', '2025-01-01 12:00:00')")
        connection.exec_driver_sql("INSERT INTO contextos_conversa VALUES (11, 'Cliente Ficticio', 'aguardando_confirmacao', 7, 1, '[{\"produto_id\":7,\"quantidade\":1}]', '2025-01-01 12:00:00')")


def snapshot(path):
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall() for table in tables}


def test_banco_vazio_cria_schema_equivalente_aos_modelos(migration_project):
    root, _, app = migration_project
    with app.app_context():
        result = upgrade_database(project_root=root)
        assert result == {"updated": True, "revision": "0006_integridade_operacoes", "backup": None}
        with db.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), db.metadata) == []
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert "ck_produto_minimo" in {c["name"] for c in sa.inspect(connection).get_check_constraints("produtos")}
        assert upgrade_database(project_root=root)["updated"] is False


def test_migracao_preserva_todos_registros_do_baseline_e_backup(migration_project):
    root, path, app = migration_project
    create_legacy(app)
    before = snapshot(path)
    with app.app_context():
        result = upgrade_database(project_root=root)
        assert snapshot(result["backup"]) == before
        assert result["backup"].stat().st_mode & 0o777 == 0o600
        assert result["backup"].is_relative_to(root / "data/artifacts/backups")
        after = snapshot(path)
        for table, records in before.items():
            if table == "produtos":
                assert [row[:8] for row in after[table]] == records
                assert after[table][0][8:] == (None, 0)
            elif table == "contextos_conversa":
                assert [row[:-1] for row in after[table]] == records
                assert all(row[-1] is None for row in after[table])
            else:
                assert after[table] == records
        assert after["operacoes_estoque"] == []
        assert upgrade_database(project_root=root)["updated"] is False
        assert len(list((root / "data/artifacts/backups").glob("*.db"))) == 1
        sucesso, _, venda = registrar_venda(7, 1, "Cliente Ficticio")
        assert sucesso and venda.id != 9
        assert db.session.get(Produto, 7).quantidade_disponivel == 19


def test_banco_antigo_com_tabela_estoque_preserva_saldo_e_evidencia(migration_project):
    root, path, app = migration_project
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE produtos (id INTEGER PRIMARY KEY, nome VARCHAR(100) NOT NULL UNIQUE, preco FLOAT NOT NULL);
            INSERT INTO produtos VALUES (3, 'Produto Antigo Ficticio', 12);
            CREATE TABLE vendas (id INTEGER PRIMARY KEY, produto_id INTEGER NOT NULL REFERENCES produtos(id),
                quantidade INTEGER NOT NULL, valor_total FLOAT NOT NULL, data_venda DATETIME);
            INSERT INTO vendas VALUES (4, 3, 1, 12, '2025-01-01 00:00:00');
            CREATE TABLE estoque (produto_id INTEGER PRIMARY KEY REFERENCES produtos(id), quantidade_disponivel INTEGER NOT NULL);
            INSERT INTO estoque VALUES (3, 13);
        """)
    with app.app_context():
        upgrade_database(project_root=root)
        product = db.session.get(Produto, 3)
        assert product.quantidade_disponivel == 13
        assert product.categoria == "sorvete simples"
        assert product.codigo_externo is None
        assert product.estoque_minimo == 0
        assert snapshot(path)["estoque"] == [(3, 13)]
        # A transferência do saldo acontece só na adoção, nunca a cada execução.
        product.quantidade_disponivel = 10
        db.session.commit()
        upgrade_database(project_root=root)
        assert db.session.get(Produto, 3).quantidade_disponivel == 10


def test_erro_de_migracao_reverte_inclusive_ddl(migration_project, monkeypatch):
    root, path, app = migration_project
    create_legacy(app)
    before = snapshot(path)

    def fail(config, revision):
        connection = config.attributes["connection"]
        connection.exec_driver_sql("CREATE TABLE tabela_parcial (id INTEGER)")
        connection.exec_driver_sql("ALTER TABLE produtos ADD COLUMN coluna_parcial TEXT")
        connection.exec_driver_sql("UPDATE produtos SET quantidade_disponivel=0")
        raise RuntimeError("VALOR_FICTICIO_QUE_NAO_DEVE_APARECER")

    monkeypatch.setattr("app.migrations.command.upgrade", fail)
    with app.app_context(), pytest.raises(MigrationError) as error:
        upgrade_database(project_root=root)
    assert "VALOR_FICTICIO" not in str(error.value)
    assert snapshot(path) == before
    assert len(list((root / "data/artifacts/backups").glob("*.db"))) == 1


def test_referencia_legada_invalida_bloqueia_e_preserva_banco(migration_project):
    root, path, app = migration_project
    create_legacy(app)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE vendas SET produto_id=999")
    before = snapshot(path)
    with app.app_context(), pytest.raises(MigrationError):
        upgrade_database(project_root=root)
    assert snapshot(path) == before


def test_cli_upgrade_current_e_create_db_compativeis(migration_project, monkeypatch):
    _, _, app = migration_project
    result = app.test_cli_runner().invoke(args=["db", "upgrade"])
    assert result.exit_code == 0, result.output
    result = app.test_cli_runner().invoke(args=["db", "current"])
    assert result.exit_code == 0
    assert "0006_integridade_operacoes" in result.output
    monkeypatch.setattr("app.app.create_app", lambda: app)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ROOT / "create_db.py"), run_name="__main__")
    assert result.value.code == 0


def create_phase_one(app, quantity):
    create_legacy(app)
    with app.app_context(), db.engine.begin() as connection:
        command.upgrade(_config(connection), "0002_modelos_tccii")
        connection.exec_driver_sql(
            "INSERT INTO movimentacoes_estoque "
            "(id, tipo_item, produto_id, tipo_movimentacao, quantidade, saldo_anterior, saldo_posterior, motivo, criado_em) "
            "VALUES (12, 'produto', 7, 'entrada', ?, 0, 1.25, 'Teste ficticio', '2025-01-01 00:00:00')",
            (quantity,),
        )


def test_novo_check_preserva_movimentacoes_indices_e_referencias(migration_project):
    root, path, app = migration_project
    create_phase_one(app, 1.25)
    before = snapshot(path)
    with app.app_context():
        upgrade_database(project_root=root)
        after = snapshot(path)
        for table in before:
            if table != "alembic_version":
                if table in {"contextos_conversa", "movimentacoes_estoque"}:
                    assert [row[:-1] for row in after[table]] == before[table]
                    assert all(row[-1] is None for row in after[table])
                else:
                    assert after[table] == before[table]
        assert after["operacoes_estoque"] == []
        with db.engine.connect() as connection:
            inspector = sa.inspect(connection)
            checks = {c["name"] for c in inspector.get_check_constraints("movimentacoes_estoque")}
            assert checks == {"ck_movimentacao_item", "ck_movimentacao_tipo", "ck_movimentacao_saldos", "ck_movimentacao_quantidade_positiva"}
            assert {i["name"] for i in inspector.get_indexes("movimentacoes_estoque")} == {
                "ix_movimentacoes_estoque_produto_id", "ix_movimentacoes_estoque_materia_prima_id",
            }
            assert len(inspector.get_foreign_keys("movimentacoes_estoque")) == 3
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


@pytest.mark.parametrize("quantity", [-1, 0])
def test_migracao_bloqueia_movimentacao_invalida_sem_alterar_dados(migration_project, quantity):
    root, path, app = migration_project
    create_phase_one(app, quantity)
    before = snapshot(path)
    with app.app_context(), pytest.raises(MigrationError):
        upgrade_database(project_root=root)
    assert snapshot(path) == before
    backups = list((root / "data/artifacts/backups").glob("*.db"))
    assert len(backups) == 1
    assert snapshot(backups[0]) == before


def create_phase_three(app, balance):
    create_phase_one(app, 1.25)
    with app.app_context(), db.engine.begin() as connection:
        command.upgrade(_config(connection), "0003_movimentacao_quantidade")
        connection.exec_driver_sql("UPDATE produtos SET quantidade_disponivel=? WHERE id=7", (balance,))
        connection.exec_driver_sql("INSERT INTO alertas_estoque (id, tipo_item, produto_id, tipo_alerta, mensagem, nivel, criado_em, ativo) VALUES (13, 'produto', 7, 'estoque_minimo', 'Teste ficticio', 'aviso', '2025-01-01', 1)")
        connection.exec_driver_sql("INSERT INTO previsoes_demanda (id, produto_id, granularidade, periodo_inicio, periodo_fim, quantidade_prevista, modelo, versao_modelo, gerada_em) VALUES (14, 7, 'diaria', '2025-01-01', '2025-01-01', 1, 'ficticio', 'v1', '2025-01-01')")


@pytest.mark.parametrize("balance", [0, 20])
def test_check_saldo_preserva_tabela_pai_e_todas_referencias(migration_project, balance):
    root, path, app = migration_project
    create_phase_three(app, balance)
    before = snapshot(path)
    with app.app_context():
        result = upgrade_database(project_root=root)
        assert snapshot(result["backup"]) == before
        after = snapshot(path)
        comparable = {k: ([row[:-1] for row in v] if k in {"contextos_conversa", "movimentacoes_estoque"} else v)
                      for k, v in after.items() if k not in {"alembic_version", "operacoes_estoque"}}
        assert comparable == {
            k: v for k, v in before.items() if k != "alembic_version"
        }
        assert all(row[-1] is None for table in ["contextos_conversa", "movimentacoes_estoque"] for row in after[table])
        assert after["operacoes_estoque"] == []
        with db.engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert {c["name"] for c in inspector.get_check_constraints("produtos")} == {"ck_produto_saldo", "ck_produto_minimo"}
            assert any(i["name"] == "ix_produtos_codigo_externo" and i["unique"] for i in inspector.get_indexes("produtos"))
            assert any(c["column_names"] == ["nome"] for c in inspector.get_unique_constraints("produtos"))
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_saldo_legado_negativo_bloqueia_sem_corrigir_e_restaura_fks(migration_project):
    root, path, app = migration_project
    create_phase_three(app, -1)
    before = snapshot(path)
    with app.app_context():
        with pytest.raises(MigrationError):
            upgrade_database(project_root=root)
        with db.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    assert snapshot(path) == before
    backups = list((root / "data/artifacts/backups").glob("*.db"))
    assert len(backups) == 1
    assert snapshot(backups[0]) == before


def create_phase_five(app):
    with app.app_context(), db.engine.begin() as connection:
        command.upgrade(_config(connection), "0005_operacoes_estoque")
        connection.exec_driver_sql("INSERT INTO produtos (id,nome,categoria,sabor,preco,descricao,quantidade_disponivel,ativo,estoque_minimo) VALUES (7,'Produto Ficticio','caixa de 10L','chocolate',10,'Descricao ficticia',20,1,0)")


def test_integridade_operacoes_migra_0005_preservando_recibos_e_auditoria(migration_project):
    root, path, app = migration_project
    create_phase_five(app)
    with app.app_context():
        estoque_service.registrar_venda([{"produto_id": 7, "quantidade": 2}], "Cliente Ficticio")
    before = snapshot(path)
    with app.app_context():
        result = upgrade_database(project_root=root)
        assert result["revision"] == "0006_integridade_operacoes"
        assert snapshot(result["backup"]) == before
        after = snapshot(path)
        assert {k: v for k, v in after.items() if k != "alembic_version"} == {
            k: v for k, v in before.items() if k != "alembic_version"}
        with db.engine.connect() as connection:
            checks = {c["name"] for c in sa.inspect(connection).get_check_constraints("operacoes_estoque")}
            assert checks == {"ck_operacao_tipo", "ck_operacao_request_hash"}
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert upgrade_database(project_root=root)["updated"] is False


@pytest.mark.parametrize(("tipo", "request_hash"), [("invalido", "a" * 64), ("venda", "curto")])
def test_integridade_operacoes_bloqueia_legado_invalido_com_backup_e_rollback(migration_project, tipo, request_hash):
    root, path, app = migration_project
    create_phase_five(app)
    with app.app_context(), db.engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO operacoes_estoque (id,tipo,request_hash,resultado,criado_em) VALUES (?,?,?,?,?)",
            ("00000000-0000-0000-0000-000000000001", tipo, request_hash, '{"ficticio":true}', "2025-01-01 00:00:00"))
    before = snapshot(path)
    with app.app_context(), pytest.raises(MigrationError):
        upgrade_database(project_root=root)
    assert snapshot(path) == before
    backups = list((root / "data/artifacts/backups").glob("*.db"))
    assert len(backups) == 1 and snapshot(backups[0]) == before
