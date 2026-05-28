from flask import Flask
from app.database import db

def create_app():

    # Cria a aplicacao Flask. O __name__ ajuda o Flask a encontrar templates e arquivos static.
    app = Flask(__name__)

    # Banco SQLite local usado pelo prototipo.
    # No Flask, sqlite:///sorvetes.db fica dentro da pasta instance/.
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sorvetes.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Conecta o objeto db ao app criado acima.
    db.init_app(app)

    # Blueprints separam as rotas por assunto. Assim o app principal fica organizado.
    from app.routes.venda_routes import venda_bp
    from app.routes.estoque_routes import estoque_bp
    from app.routes.produto_routes import produto_bp

    app.register_blueprint(venda_bp)
    app.register_blueprint(estoque_bp)
    app.register_blueprint(produto_bp)

    return app
