"""Migrações verificadas em SQLite temporário, somente com dados fictícios."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import runpy
import sqlite3
import subprocess

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app.app import create_app
from app.database import db
from app.migrations import MigrationError, upgrade_database
from app.models.produto import Produto
from app.services.venda_service import registrar_venda


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
        assert result == {"updated": True, "revision": "0002_modelos_tccii", "backup": None}
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
            else:
                assert after[table] == records
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
    assert "0002_modelos_tccii" in result.output
    monkeypatch.setattr("app.app.create_app", lambda: app)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(ROOT / "create_db.py"), run_name="__main__")
    assert result.value.code == 0
