from flask import Flask
from app.database import db

def create_app(test_config=None):

    # Cria a aplicacao Flask. O __name__ ajuda o Flask a encontrar templates e arquivos static.
    app = Flask(__name__)

    # Banco SQLite local usado pelo prototipo.
    # No Flask, sqlite:///sorvetes.db fica dentro da pasta instance/.
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sorvetes.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Os testes substituem o banco antes de inicializar o SQLAlchemy.
    # Sem configuração explícita, a aplicação mantém o banco local habitual.
    if test_config is not None:
        app.config.update(test_config)

    # Conecta o objeto db ao app criado acima.
    db.init_app(app)

    # Registra todos os modelos antes de criar tabelas ou usar migrações.
    from app import models
    from app.migrations import database_commands
    app.cli.add_command(database_commands)

    # Blueprints separam as rotas por assunto. Assim o app principal fica organizado.
    from app.routes.venda_routes import venda_bp
    from app.routes.estoque_routes import estoque_bp
    from app.routes.produto_routes import produto_bp

    app.register_blueprint(venda_bp)
    app.register_blueprint(estoque_bp)
    app.register_blueprint(produto_bp)

    from app.agents.base_agent import AgentUnavailable, RemoteFailure

    @app.errorhandler(AgentUnavailable)
    def runtime_indisponivel(error):
        return {"erro": "Atendimento temporariamente indisponível. Tente novamente."}, 503

    @app.errorhandler(RemoteFailure)
    def falha_agente(error):
        return {"erro": "Não foi possível concluir a operação solicitada."}, 503

    return app
