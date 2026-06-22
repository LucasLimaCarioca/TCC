from app.database import db
from app.models.produto import Produto
from app.models.venda import Venda


def registrar_venda(produto_id, quantidade, cliente_nome="Cliente Simulado"):
    # Regra central da fase 3: validar produto, salvar venda e atualizar estoque.
    # Esta função é um atalho para o caso mais simples: venda de um único produto.
    # Internamente ela reaproveita a função de múltiplos itens para evitar duplicação.
    sucesso, mensagem, vendas = registrar_vendas_multiplas(
        [
            {
                "produto_id": produto_id,
                "quantidade": quantidade
            }
        ],
        cliente_nome=cliente_nome
    )

    # A função de múltiplos itens sempre retorna uma lista.
    # Para a venda simples, devolvemos apenas o primeiro registro.
    venda = vendas[0] if vendas else None

    return sucesso, mensagem, venda


def registrar_vendas_multiplas(itens, cliente_nome="Cliente Simulado"):
    # Registra um pedido com um ou mais produtos.
    # Primeiro valida todos os itens; só depois altera estoque e salva vendas.
    # Isso evita venda parcial: se um item falhar, nenhum item é registrado.
    produtos_por_id = {}

    for item in itens:
        produto_id = item["produto_id"]
        quantidade = item["quantidade"]

        # Busca o produto pela chave primária recebida na rota ou pelo agente.
        produto = db.session.get(Produto, produto_id)

        # Produto inexistente ou inativo não pode ser vendido.
        if produto is None or not produto.ativo:
            return False, "Produto não encontrado.", []

        # Quantidades zero ou negativas são rejeitadas antes de qualquer alteração no banco.
        if quantidade <= 0:
            return False, "Informe uma quantidade maior que zero.", []

        # Validação de estoque antes de gravar a venda.
        # Como ainda estamos no protótipo, o estoque fica em Produto.quantidade_disponivel.
        if produto.quantidade_disponivel < quantidade:
            # Estoque zerado recebe uma mensagem própria.
            # Nesse caso não faz sentido sugerir uma quantidade menor do mesmo item.
            if produto.quantidade_disponivel == 0:
                mensagem = (
                    f"No momento não temos {produto.nome} em estoque."
                )
                return False, mensagem, []

            mensagem = (
                f"No momento temos apenas "
                f"{produto.quantidade_disponivel} unidades de {produto.nome}."
            )
            return False, mensagem, []

        # Guarda o objeto Produto para reutilizar na etapa de gravação,
        # evitando uma nova consulta ao banco.
        produtos_por_id[produto_id] = produto

    vendas = []

    # Só chegamos aqui se todos os itens passaram nas validações.
    for item in itens:
        produto = produtos_por_id[item["produto_id"]]
        quantidade = item["quantidade"]
        valor_total = produto.preco * quantidade

        # Cada item do pedido vira um registro na tabela vendas.
        # Exemplo: 2 caixas 10L chocolate + 1 caixa 5L morango = 2 vendas.
        venda = Venda(
            cliente_nome=cliente_nome,
            produto_id=produto.id,
            quantidade=quantidade,
            valor_total=valor_total
        )

        # Atualiza o estoque simplificado no próprio cadastro do produto.
        produto.quantidade_disponivel -= quantidade

        db.session.add(venda)
        vendas.append(venda)

    # Commit único para salvar todas as vendas e alterações de estoque juntas.
    db.session.commit()

    return True, "Venda registrada com sucesso.", vendas
