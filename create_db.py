"""Entrada compatível para criar/atualizar o banco pelas migrações Alembic."""

from app.app import create_app


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        app.cli.main(args=["db", "upgrade"], prog_name="create_db.py")
