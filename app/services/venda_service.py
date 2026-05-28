from app.database import db
from app.models.produto import Produto
from app.models.venda import Venda


def registrar_venda(produto_id, quantidade, cliente_nome="Cliente Simulado"):
    # Regra central da fase 3: validar produto, salvar venda e atualizar estoque.
    produto = db.session.get(Produto, produto_id)

    if produto is None or not produto.ativo:
        return False, "Produto não encontrado.", None

    if quantidade <= 0:
        return False, "Informe uma quantidade maior que zero.", None

    if produto.quantidade_disponivel < quantidade:
        mensagem = (
            f"No momento temos apenas "
            f"{produto.quantidade_disponivel} unidades de {produto.nome}."
        )
        return False, mensagem, None

    valor_total = produto.preco * quantidade

    venda = Venda(
        cliente_nome=cliente_nome,
        produto_id=produto.id,
        quantidade=quantidade,
        valor_total=valor_total
    )

    produto.quantidade_disponivel -= quantidade

    db.session.add(venda)
    db.session.commit()

    return True, "Venda registrada com sucesso.", venda
