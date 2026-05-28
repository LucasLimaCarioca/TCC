from app.app import create_app
from app.database import db

from app.models.cliente import Cliente
from app.models.produto import Produto


app = create_app()


with app.app_context():

    # Dados iniciais da fase 2.
    # O seed e idempotente: pode rodar de novo sem duplicar esses produtos.
    produtos_iniciais = [
        {
            "nome": "Sorvete de Chocolate",
            "preco": 15.0,
            "descricao": "Sorvete cremoso sabor chocolate.",
            "quantidade": 20,
            "ativo": True
        },
        {
            "nome": "Sorvete de Morango",
            "preco": 16.0,
            "descricao": "Sorvete de morango com sabor frutado.",
            "quantidade": 7,
            "ativo": True
        },
        {
            "nome": "Sorvete de Baunilha",
            "preco": 14.0,
            "descricao": "Sorvete classico de baunilha.",
            "quantidade": 15,
            "ativo": True
        }
    ]

    for item in produtos_iniciais:

        produtos_com_mesmo_nome = Produto.query.filter_by(
            nome=item["nome"]
        ).order_by(Produto.id).all()

        produto = produtos_com_mesmo_nome[0] if produtos_com_mesmo_nome else None

        # Remove duplicatas antigas criadas por execucoes repetidas do seed anterior.
        # Mantem qualquer duplicata que ja tenha venda vinculada.
        for produto_duplicado in produtos_com_mesmo_nome[1:]:
            if not produto_duplicado.vendas:
                db.session.delete(produto_duplicado)

        if produto is None:
            produto = Produto(
                nome=item["nome"],
                preco=item["preco"],
                descricao=item["descricao"],
                quantidade_disponivel=item["quantidade"],
                ativo=item["ativo"]
            )

            db.session.add(produto)
        else:
            produto.preco = item["preco"]
            produto.descricao = item["descricao"]
            produto.quantidade_disponivel = item["quantidade"]
            produto.ativo = item["ativo"]

    clientes_iniciais = [
        {
            "nome": "Cliente Simulado",
            "telefone": "92999990000"
        },
        {
            "nome": "Cliente Simulado 2",
            "telefone": "92999990001"
        },
        {
            "nome": "Cliente Simulado 3",
            "telefone": "92999990002"
        }
    ]

    for item in clientes_iniciais:
        cliente = Cliente.query.filter_by(
            nome=item["nome"]
        ).first()

        if cliente is None:
            cliente = Cliente(
                nome=item["nome"],
                telefone=item["telefone"]
            )

            db.session.add(cliente)
        else:
            cliente.telefone = item["telefone"]

    db.session.commit()

    print("Produtos e clientes cadastrados!")
