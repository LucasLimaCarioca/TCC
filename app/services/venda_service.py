from app.database import db
from app.models.produto import Produto
from app.models.venda import Venda


def registrar_venda(produto_id, quantidade, cliente_nome="Cliente Simulado"):
    # Regra central da fase 3: validar produto, salvar venda e atualizar estoque.
    sucesso, mensagem, vendas = registrar_vendas_multiplas(
        [
            {
                "produto_id": produto_id,
                "quantidade": quantidade
            }
        ],
        cliente_nome=cliente_nome
    )

    venda = vendas[0] if vendas else None

    return sucesso, mensagem, venda


def registrar_vendas_multiplas(itens, cliente_nome="Cliente Simulado"):
    # Registra um pedido com um ou mais produtos.
    # Primeiro valida todos os itens; só depois altera estoque e salva vendas.
    produtos_por_id = {}

    for item in itens:
        produto_id = item["produto_id"]
        quantidade = item["quantidade"]
        produto = db.session.get(Produto, produto_id)

        if produto is None or not produto.ativo:
            return False, "Produto não encontrado.", []

        if quantidade <= 0:
            return False, "Informe uma quantidade maior que zero.", []

        if produto.quantidade_disponivel < quantidade:
            mensagem = (
                f"No momento temos apenas "
                f"{produto.quantidade_disponivel} unidades de {produto.nome}."
            )
            return False, mensagem, []

        produtos_por_id[produto_id] = produto

    vendas = []

    for item in itens:
        produto = produtos_por_id[item["produto_id"]]
        quantidade = item["quantidade"]
        valor_total = produto.preco * quantidade

        venda = Venda(
            cliente_nome=cliente_nome,
            produto_id=produto.id,
            quantidade=quantidade,
            valor_total=valor_total
        )

        produto.quantidade_disponivel -= quantidade

        db.session.add(venda)
        vendas.append(venda)

    db.session.commit()

    return True, "Venda registrada com sucesso.", vendas
