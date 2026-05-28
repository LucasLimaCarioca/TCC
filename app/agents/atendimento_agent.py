from app.models.produto import Produto
from app.services.venda_service import registrar_venda
import re


class AtendimentoAgent:

    def responder(self, mensagem):

        mensagem = mensagem.lower()

        if "oi" in mensagem:
            return "Olá! Como posso ajudar?"

        if "sabores" in mensagem:

            produtos = Produto.query.filter_by(
                ativo=True
            ).order_by(Produto.nome).all()

            lista = "Sabores disponíveis:\n"

            for produto in produtos:
                lista += f"- {produto.nome}\n"

            return lista

        if "preço" in mensagem or "preco" in mensagem:

            produtos = Produto.query.filter_by(
                ativo=True
            ).order_by(Produto.nome).all()

            resposta = "Tabela de preços:\n"

            for produto in produtos:
                resposta += (
                    f"{produto.nome}: "
                    f"R$ {produto.preco}\n"
                )

            return resposta

        if "dispon" in mensagem or "tem " in mensagem:

            produtos = Produto.query.filter_by(
                ativo=True
            ).order_by(Produto.nome).all()

            resposta = "Disponibilidade atual:\n"

            for produto in produtos:
                resposta += (
                    f"{produto.nome}: "
                    f"{produto.quantidade_disponivel} unidades\n"
                )

            return resposta

        if "comprar" in mensagem or "quero" in mensagem or "pedido" in mensagem:
            produto = self._encontrar_produto_na_mensagem(mensagem)
            quantidade = self._extrair_quantidade(mensagem)

            if produto is not None:
                return self.registrar_venda(
                    produto.nome,
                    quantidade,
                    cliente_nome="Cliente Simulado"
                )

        return (
            "Desculpe, não entendi sua solicitação."
        )

    def registrar_venda(
        self,
        produto_nome,
        quantidade,
        cliente_nome="Cliente Simulado"
    ):

        produto = Produto.query.filter_by(
            nome=produto_nome
        ).first()

        if not produto:
            return "Produto não encontrado."

        if quantidade <= 0:
            return "Informe uma quantidade maior que zero."

        if produto.quantidade_disponivel < quantidade:
            return (
                f"No momento temos apenas "
                f"{produto.quantidade_disponivel} unidades de {produto.nome}."
            )

        valor_total = produto.preco * quantidade

        sucesso, mensagem, venda = registrar_venda(
            produto.id,
            quantidade,
            cliente_nome=cliente_nome,
        )

        if not sucesso:
            return mensagem

        return (
            f"Venda registrada!\n"
            f"Produto: {produto.nome}\n"
            f"Quantidade: {quantidade}\n"
            f"Total: R$ {valor_total}"
        )

    def _encontrar_produto_na_mensagem(self, mensagem):
        produtos = Produto.query.filter_by(
            ativo=True
        ).all()

        for produto in produtos:
            nome = produto.nome.lower()
            sabor = nome.replace("sorvete de ", "")

            if nome in mensagem or sabor in mensagem:
                return produto

        return None

    def _extrair_quantidade(self, mensagem):
        resultado = re.search(r"\d+", mensagem)

        if resultado is None:
            return 1

        return int(resultado.group())
