from flask import Flask
from app.database import db

def create_app():

    app = Flask(__name__)

    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sorvetes.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)

    from app.routes.venda_routes import venda_bp

    app.register_blueprint(venda_bp)

    return app