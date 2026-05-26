from app.models.produto import Produto
from app.models.venda import Venda
from app.database import db

class AtendimentoAgent:

    def responder(self, mensagem):

        mensagem = mensagem.lower()

        if "oi" in mensagem:
            return "Olá! Como posso ajudar?"

        if "sabores" in mensagem:

            produtos = Produto.query.all()

            lista = "Sabores disponíveis:\n"

            for produto in produtos:
                lista += f"- {produto.nome}\n"

            return lista

        if "preço" in mensagem:

            produtos = Produto.query.all()

            resposta = "Tabela de preços:\n"

            for produto in produtos:
                resposta += (
                    f"{produto.nome}: "
                    f"R$ {produto.preco}\n"
                )

            return resposta

        return (
            "Desculpe, não entendi sua solicitação."
        )

    def registrar_venda(
        self,
        produto_nome,
        quantidade
    ):

        produto = Produto.query.filter_by(
            nome=produto_nome
        ).first()

        if not produto:
            return "Produto não encontrado."

        valor_total = produto.preco * quantidade

        venda = Venda(
            produto=produto.nome,
            quantidade=quantidade,
            valor_total=valor_total
        )

        db.session.add(venda)
        db.session.commit()

        return (
            f"Venda registrada!\n"
            f"Produto: {produto.nome}\n"
            f"Quantidade: {quantidade}\n"
            f"Total: R$ {valor_total}"
        )