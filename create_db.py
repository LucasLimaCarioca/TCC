from sqlalchemy import text

from app.app import create_app
from app.database import db

from app.models.cliente import Cliente
from app.models.historico_conversa import HistoricoConversa
from app.models.produto import Produto
from app.models.venda import Venda


app = create_app()


def tabela_existe(nome_tabela):
    resultado = db.session.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = :nome"
        ),
        {"nome": nome_tabela}
    ).first()

    return resultado is not None


def coluna_existe(nome_tabela, nome_coluna):
    resultado = db.session.execute(
        text(f"PRAGMA table_info({nome_tabela})")
    )

    return nome_coluna in [linha[1] for linha in resultado]


def adicionar_coluna_se_precisar(nome_tabela, nome_coluna, definicao):
    if not coluna_existe(nome_tabela, nome_coluna):
        db.session.execute(
            text(f"ALTER TABLE {nome_tabela} ADD COLUMN {nome_coluna} {definicao}")
        )


def migrar_banco_existente():
    # create_all cria tabelas novas, mas nao altera tabelas antigas.
    # Esta funcao faz apenas ajustes simples para o SQLite do prototipo.
    adicionar_coluna_se_precisar(
        "produtos",
        "descricao",
        "VARCHAR(255)"
    )
    adicionar_coluna_se_precisar(
        "produtos",
        "quantidade_disponivel",
        "INTEGER NOT NULL DEFAULT 0"
    )
    adicionar_coluna_se_precisar(
        "produtos",
        "ativo",
        "BOOLEAN NOT NULL DEFAULT 1"
    )
    adicionar_coluna_se_precisar(
        "vendas",
        "cliente_nome",
        "VARCHAR(100) NOT NULL DEFAULT 'Cliente Simulado'"
    )

    if tabela_existe("estoque"):
        db.session.execute(
            text(
                "UPDATE produtos "
                "SET quantidade_disponivel = ("
                "SELECT estoque.quantidade_disponivel "
                "FROM estoque "
                "WHERE estoque.produto_id = produtos.id"
                ") "
                "WHERE id IN (SELECT produto_id FROM estoque)"
            )
        )
        db.session.execute(text("DROP TABLE estoque"))

    db.session.commit()


with app.app_context():

    # Cria somente as tabelas que ainda nao existem.
    # Esse comando nao apaga dados ja salvos no arquivo instance/sorvetes.db.
    db.create_all()
    migrar_banco_existente()

    print("Banco criado com sucesso!")
