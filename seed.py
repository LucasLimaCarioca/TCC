from app.app import create_app
from app.database import db

from app.models.cliente import Cliente
from app.models.produto import Produto


app = create_app()


def nome_produto(categoria, sabor):
    return f"{categoria} - {sabor}"


with app.app_context():

    # Dados iniciais da fábrica simulada.
    # A combinação categoria + sabor permite produtos com o mesmo sabor e tamanhos diferentes.
    produtos_iniciais = [
        {
            "categoria": "caixa de 10L",
            "sabor": "morango",
            "preco": 101.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de 10L",
            "sabor": "chocolate",
            "preco": 102.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de 10L",
            "sabor": "baunilha",
            "preco": 100.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de 5L",
            "sabor": "morango",
            "preco": 66.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de 5L",
            "sabor": "chocolate",
            "preco": 67.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de 5L",
            "sabor": "baunilha",
            "preco": 65.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de sundae",
            "sabor": "morango",
            "preco": 45.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de sundae",
            "sabor": "chocolate",
            "preco": 48.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de picole",
            "sabor": "caixa A",
            "preco": 120.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de picole",
            "sabor": "caixa B",
            "preco": 120.0,
            "quantidade": 20
        },
        {
            "categoria": "caixa de picole",
            "sabor": "caixa C",
            "preco": 120.0,
            "quantidade": 20
        }
    ]

    for item in produtos_iniciais:
        nome = nome_produto(
            item["categoria"],
            item["sabor"]
        )

        produto = Produto.query.filter_by(
            nome=nome
        ).first()

        if produto is None:
            produto = Produto(
                nome=nome
            )

            db.session.add(produto)

        produto.categoria = item["categoria"]
        produto.sabor = item["sabor"]
        produto.preco = item["preco"]
        produto.descricao = (
            f"{item['categoria']} sabor {item['sabor']}."
        )
        produto.quantidade_disponivel = item["quantidade"]
        produto.ativo = True

    nomes_ativos = [
        nome_produto(item["categoria"], item["sabor"])
        for item in produtos_iniciais
    ]

    # Produtos antigos do prototipo ficam inativos para não confundirem o agente.
    # Mantemos os registros para preservar vendas históricas já associadas a eles.
    for produto in Produto.query.all():
        if produto.nome not in nomes_ativos:
            produto.ativo = False

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
