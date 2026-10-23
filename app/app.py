from flask import Flask
from app.database import db
from decimal import Decimal
import os
from pathlib import Path

from app.services.preparacao_dados_service import private_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _database_uri():
    """Prepara somente o banco privado padrão, sem transferir bancos legados."""
    database_path = private_path(PROJECT_ROOT, "data/artifacts/sorvetes.db")
    if not database_path.exists() and (PROJECT_ROOT / "instance/sorvetes.db").exists():
        raise RuntimeError(
            "O banco legado está em instance/sorvetes.db. Transfira-o com backup "
            "para data/artifacts/sorvetes.db antes de iniciar a aplicação."
        )
    for directory in (database_path.parent.parent, database_path.parent):
        directory.mkdir(mode=0o700, exist_ok=True)
    if not database_path.exists():
        descriptor = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    return f"sqlite:///{database_path}"


def create_app(test_config=None):

    # Cria a aplicacao Flask. O __name__ ajuda o Flask a encontrar templates e arquivos static.
    app = Flask(__name__)

    @app.template_filter("quantidade")
    def formatar_quantidade(value):
        formatted = format(Decimal(str(value)), "f")
        return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Uma URI explícita nunca prepara nem acessa o banco privado padrão.
    if test_config is not None:
        app.config.update(test_config)
    if 'SQLALCHEMY_DATABASE_URI' not in app.config:
        app.config['SQLALCHEMY_DATABASE_URI'] = (
            'sqlite:///:memory:' if app.testing else _database_uri()
        )

    # Conecta o objeto db ao app criado acima.
    db.init_app(app)

    # Registra todos os modelos antes de criar tabelas ou usar migrações.
    from app import models
    from app.migrations import database_commands
    app.cli.add_command(database_commands)
    from app.forecast_commands import forecast_commands
    app.cli.add_command(forecast_commands)

    # Blueprints separam as rotas por assunto. Assim o app principal fica organizado.
    from app.routes.venda_routes import venda_bp
    from app.routes.estoque_routes import estoque_bp
    from app.routes.produto_routes import produto_bp
    from app.routes.previsao_routes import previsao_bp, protect_forecast_access_logs

    app.register_blueprint(venda_bp)
    app.register_blueprint(estoque_bp)
    app.register_blueprint(produto_bp)
    app.register_blueprint(previsao_bp)
    protect_forecast_access_logs()

    from app.agents.base_agent import AgentUnavailable, RemoteFailure
    from app.services.estoque_service import EstoqueError

    @app.errorhandler(EstoqueError)
    def estoque_invalido(error):
        status = 409 if error.code in {"IDEMPOTENCY_CONFLICT", "DUPLICATE_MATERIAL"} else 400
        return {"erro": str(error), "code": error.code}, status

    @app.errorhandler(AgentUnavailable)
    def runtime_indisponivel(error):
        return {"erro": "Atendimento temporariamente indisponível. Tente novamente."}, 503

    @app.errorhandler(RemoteFailure)
    def falha_agente(error):
        return {"erro": "Não foi possível concluir a operação solicitada."}, 503

    return app
