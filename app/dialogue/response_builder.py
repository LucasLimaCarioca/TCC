"""Respostas textuais do TCC I; não consulta o banco nem altera estado."""


class ResponseBuilder:
    """Formata produtos, itens e vendas com os textos e valores do protótipo."""

    def saudacao(self):
        return (
            "Olá! Sou o agente de atendimento da sorveteria.\n"
            "Posso informar sabores, preços, disponibilidade ou registrar um pedido."
        )

    def intencao_desconhecida(self):
        return (
            "Desculpe, não entendi sua solicitação.\n"
            "Você pode perguntar por sabores, preços, disponibilidade ou fazer um pedido."
        )

    def sabores(self, produtos):
        # Lista os sabores agrupados por categoria/tamanho.
        # Isso é importante porque o mesmo sabor pode existir em caixa de 10L, 5L etc.

        if not produtos:
            return "No momento não há sabores cadastrados."

        resposta = "Produtos disponíveis por categoria:\n"
        categoria_atual = None

        for produto in produtos:
            if produto.categoria != categoria_atual:
                categoria_atual = produto.categoria
                resposta += f"\n{categoria_atual}:\n"

            resposta += f"- {produto.sabor}\n"

        return resposta

    def tabela_precos(self, produtos):
        if not produtos:
            return "No momento não há produtos cadastrados."

        resposta = "Tabela de preços:\n"
        categoria_atual = None

        for produto in produtos:
            if produto.categoria != categoria_atual:
                categoria_atual = produto.categoria
                resposta += f"\n{categoria_atual}:\n"

            resposta += (
                f"- {produto.sabor}: "
                f"R$ {produto.preco:.2f}\n"
            )

        return resposta

    def precos_produtos(self, produtos):
        # Formata preço específico para um ou poucos produtos.
        # Até 3 itens cabem bem na conversa; acima disso a tabela completa é melhor.
        if len(produtos) == 1:
            produto = produtos[0]

            return (
                f"O valor de {produto.nome} é R$ {produto.preco:.2f}."
            )

        resposta = "Valores dos produtos solicitados:\n"

        for produto in produtos:
            resposta += (
                f"- {produto.nome}: R$ {produto.preco:.2f}\n"
            )

        return resposta

    def disponibilidade(self, produtos, produto_especifico=None):
        if produto_especifico is not None:
            if produto_especifico.quantidade_disponivel <= 0:
                return (
                    f"No momento não temos {produto_especifico.nome} em estoque."
                )

            return (
                f"Sim, temos {produto_especifico.quantidade_disponivel} "
                f"unidades de {produto_especifico.nome} em estoque."
            )

        # Sem produto específico, mostra a quantidade disponível de cada produto ativo.

        if not produtos:
            return "No momento não há produtos ativos para consulta."

        resposta = "Disponibilidade atual:\n"

        for produto in produtos:
            resposta += (
                f"{produto.categoria} - {produto.sabor}: "
                f"{produto.quantidade_disponivel} unidades\n"
            )

        return resposta

    def confirmacao_pedido(self, itens):
        # Mensagem enviada antes de registrar a venda.
        # O usuário precisa confirmar com "sim".
        return (
            "Encontrei este pedido:\n"
            f"{self.resumo_itens(itens)}"
            "Confirma a compra? Responda sim ou não."
        )

    def resumo_itens(self, itens):
        # Gera o resumo textual de um pedido, usado tanto na confirmação
        # quanto quando o usuário envia algo diferente de sim/não.
        resposta = ""
        total = 0

        for item in itens:
            produto = item["produto"]
            quantidade = item["quantidade"]
            subtotal = produto.preco * quantidade
            total += subtotal
            resposta += (
                f"- {produto.nome}: "
                f"{quantidade} unidade(s) "
                f"(R$ {subtotal:.2f})\n"
            )

        resposta += f"Total: R$ {total:.2f}\n"

        return resposta

    def venda_registrada(self, venda):
        return (
            f"Venda registrada!\n"
            f"Produto: {venda.produto.nome}\n"
            f"Quantidade: {venda.quantidade}\n"
            f"Total: R$ {venda.valor_total}"
        )

    def pedido_registrado(self, vendas):
        # Monta uma resposta amigável com todos os itens registrados.
        resposta = "Venda registrada!\n"
        total_pedido = 0

        for venda in vendas:
            total_pedido += venda.valor_total
            resposta += (
                f"- {venda.produto.nome}: "
                f"{venda.quantidade} unidade(s) "
                f"(R$ {venda.valor_total:.2f})\n"
            )

        resposta += f"Total do pedido: R$ {total_pedido:.2f}"

        return resposta

    def confirmacao_sem_contexto(self):
        return (
            "Não encontrei nenhum pedido aguardando confirmação.\n"
            "Para iniciar um pedido, envie algo como: quero 2 caixas de 10L chocolate."
        )

    def pedido_nao_identificado(self):
        return (
            "Não consegui identificar os produtos do pedido.\n"
            "Exemplo: quero 2 caixas de 10L chocolate e 1 caixa de sundae morango."
        )

    def cancelamento(self):
        return "Pedido cancelado. Posso ajudar com outra coisa?"

    def lembrete(self, itens):
        return (
            "Ainda tenho um pedido aguardando confirmação:\n"
            f"{self.resumo_itens(itens)}"
            "Responda sim para confirmar ou não para cancelar."
        )

    def sabor_ambiguo(self, sabor, produtos):
        categorias = ", ".join(produto.categoria for produto in produtos)
        return (
            f"Encontrei o sabor {sabor} em mais de uma categoria: {categorias}.\n"
            "Informe também a categoria/tamanho.\n"
            f"Exemplo: quero 2 caixas de 10L {sabor}."
        )

    def estoque_insuficiente(self, produto):
        if produto.quantidade_disponivel == 0:
            return (
                f"No momento não temos {produto.nome} em estoque.\n"
                "Você pode escolher outro sabor ou categoria."
            )
        return (
            f"No momento temos apenas "
            f"{produto.quantidade_disponivel} unidades de {produto.nome}.\n"
            "Você pode pedir uma quantidade menor ou escolher outro sabor."
        )

    def produto_nao_encontrado(self):
        return "Produto não encontrado."
