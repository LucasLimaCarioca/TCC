"""Migrações Alembic locais com backup SQLite e transação explícita."""

import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import click
from flask.cli import with_appcontext
from sqlalchemy import inspect

from app.database import db
from app.services.preparacao_dados_service import private_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MigrationError(RuntimeError):
    pass


def _config(connection):
    config = Config()
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.attributes["connection"] = connection
    return config


def _backup(database_path, project_root):
    directory = private_path(project_root, "data/artifacts/backups")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = private_path(project_root, directory / ("banco-" + uuid4().hex + ".db"))
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        source = sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
    except Exception:
        target.unlink()
        raise
    return target


def upgrade_database(*, project_root=None):
    """Atualiza somente SQLite; bancos existentes recebem backup privado antes."""
    project_root = Path(project_root or PROJECT_ROOT).resolve()
    if db.engine.dialect.name != "sqlite":
        raise MigrationError("Esta fase suporta migrações apenas em SQLite.")
    backup = None
    with db.engine.connect() as connection:
        config = _config(connection)
        head = ScriptDirectory.from_config(config).get_current_head()
        revision = MigrationContext.configure(connection).get_current_revision()
        if revision == head:
            return {"updated": False, "revision": head, "backup": None}
        existing_tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        database_path = db.engine.url.database
        if existing_tables and database_path not in (None, "", ":memory:"):
            backup = _backup(database_path, project_root)
        # PRAGMA/inspeções anteriores iniciam a transação SQLAlchemy implicitamente.
        connection.rollback()
        # O batch SQLite precisa reconstruir tabelas referenciadas por FKs.
        # O PRAGMA deve mudar antes do BEGIN; somente esta conexão é afetada.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            # SQLite legado não inclui DDL em transações implícitas do sqlite3.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            command.upgrade(config, "head")
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise MigrationError("Há referências inválidas no banco; revisão necessária.")
            connection.commit()
        except Exception as error:
            connection.rollback()
            suffix = f" Backup local: {backup.relative_to(project_root)}." if backup else ""
            raise MigrationError(
                f"Migração cancelada ({type(error).__name__}); alterações revertidas." + suffix
            ) from None
        finally:
            # Nunca devolve à pool uma conexão com as referências desabilitadas.
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
    return {"updated": True, "revision": head, "backup": backup}


@click.group("db")
def database_commands():
    """Atualiza ou consulta a versão do banco local."""


@database_commands.command("upgrade")
@with_appcontext
def upgrade_command():
    try:
        result = upgrade_database()
    except Exception as error:
        if isinstance(error, MigrationError):
            raise click.ClickException(str(error)) from None
        raise click.ClickException("Não foi possível validar ou preparar o backup local.") from None
    click.echo(f"Banco na revisão {result['revision']}.")
    if result["backup"]:
        click.echo(f"Backup local: {result['backup'].relative_to(PROJECT_ROOT)}")


@database_commands.command("current")
@with_appcontext
def current_command():
    with db.engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    click.echo(revision or "Banco ainda sem revisão Alembic.")
