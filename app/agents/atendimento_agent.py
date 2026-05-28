import re

from app.models.produto import Produto
from app.services.venda_service import registrar_venda


class AtendimentoAgent:
    # Protótipo de agente da fase 4.
    # Ele ainda não usa SPADE: simula o comportamento de um agente com métodos bem separados.
    # Fluxo: receber mensagem -> interpretar intenção -> consultar banco -> gerar resposta.

    def responder(self, mensagem, cliente_nome="Cliente Simulado"):
        mensagem_normalizada = self._normalizar_mensagem(mensagem)
        intencao = self._interpretar_intencao(mensagem_normalizada)

        if intencao == "saudacao":
            return self._responder_saudacao()

        if intencao == "consultar_sabores":
            return self._responder_sabores()

        if intencao == "consultar_precos":
            return self._responder_precos()

        if intencao == "consultar_disponibilidade":
            return self._responder_disponibilidade()

        if intencao == "registrar_venda":
            return self._processar_pedido(
                mensagem_normalizada,
                cliente_nome
            )

        return self._responder_intencao_desconhecida()

    def registrar_venda(
        self,
        produto_nome,
        quantidade,
        cliente_nome="Cliente Simulado"
    ):
        # Método público mantido para outras partes do sistema poderem pedir uma venda ao agente.
        produto = Produto.query.filter_by(
            nome=produto_nome
        ).first()

        if produto is None:
            return "Produto não encontrado."

        sucesso, mensagem, venda = registrar_venda(
            produto.id,
            quantidade,
            cliente_nome=cliente_nome
        )

        if not sucesso:
            return mensagem

        return (
            f"Venda registrada!\n"
            f"Produto: {venda.produto.nome}\n"
            f"Quantidade: {venda.quantidade}\n"
            f"Total: R$ {venda.valor_total}"
        )

    def _normalizar_mensagem(self, mensagem):
        # Normalizar evita repetir lower/strip em cada regra de intenção.
        return mensagem.lower().strip()

    def _interpretar_intencao(self, mensagem):
        # Interpretação simples por palavras-chave.
        # Na fase 4, isso já é suficiente para demonstrar o conceito de agente.
        if self._contem_termo(mensagem, ["oi", "ola", "olá", "bom dia", "boa tarde"]):
            return "saudacao"

        if any(palavra in mensagem for palavra in ["sabores", "sabor", "opcoes", "opções"]):
            return "consultar_sabores"

        if any(palavra in mensagem for palavra in ["preço", "preco", "valor", "quanto custa"]):
            return "consultar_precos"

        if any(palavra in mensagem for palavra in ["dispon", "tem ", "estoque"]):
            return "consultar_disponibilidade"

        if any(palavra in mensagem for palavra in ["comprar", "quero", "pedido", "levar"]):
            return "registrar_venda"

        return "desconhecida"

    def _buscar_produtos_ativos(self):
        # Consulta centralizada para manter as respostas sempre baseadas no banco.
        return Produto.query.filter_by(
            ativo=True
        ).order_by(Produto.nome).all()

    def _responder_saudacao(self):
        return (
            "Olá! Sou o agente de atendimento da sorveteria.\n"
            "Posso informar sabores, preços, disponibilidade ou registrar um pedido."
        )

    def _responder_sabores(self):
        produtos = self._buscar_produtos_ativos()

        if not produtos:
            return "No momento não há sabores cadastrados."

        resposta = "Sabores disponíveis:\n"

        for produto in produtos:
            resposta += f"- {produto.nome}\n"

        return resposta

    def _responder_precos(self):
        produtos = self._buscar_produtos_ativos()

        if not produtos:
            return "No momento não há produtos cadastrados."

        resposta = "Tabela de preços:\n"

        for produto in produtos:
            resposta += (
                f"{produto.nome}: "
                f"R$ {produto.preco:.2f}\n"
            )

        return resposta

    def _responder_disponibilidade(self):
        produtos = self._buscar_produtos_ativos()

        if not produtos:
            return "No momento não há produtos ativos para consulta."

        resposta = "Disponibilidade atual:\n"

        for produto in produtos:
            resposta += (
                f"{produto.nome}: "
                f"{produto.quantidade_disponivel} unidades\n"
            )

        return resposta

    def _processar_pedido(self, mensagem, cliente_nome):
        produto = self._encontrar_produto_na_mensagem(mensagem)
        quantidade = self._extrair_quantidade(mensagem)

        if produto is None:
            return (
                "Não consegui identificar o sabor do pedido.\n"
                "Exemplo: quero 2 sorvetes de chocolate."
            )

        return self.registrar_venda(
            produto.nome,
            quantidade,
            cliente_nome=cliente_nome
        )

    def _responder_intencao_desconhecida(self):
        return (
            "Desculpe, não entendi sua solicitação.\n"
            "Você pode perguntar por sabores, preços, disponibilidade ou fazer um pedido."
        )

    def _encontrar_produto_na_mensagem(self, mensagem):
        produtos = self._buscar_produtos_ativos()

        for produto in produtos:
            nome = produto.nome.lower()
            sabor = nome.replace("sorvete de ", "")

            if nome in mensagem or sabor in mensagem:
                return produto

        return None

    def _extrair_quantidade(self, mensagem):
        # Se a mensagem não tiver número, assume 1 unidade para manter o fluxo simples.
        resultado = re.search(r"\d+", mensagem)

        if resultado is None:
            return 1

        return int(resultado.group())

    def _contem_termo(self, mensagem, termos):
        # Procura termos como palavras/expressões completas.
        # Isso evita classificar "chocolate" como saudação por conter "ola".
        for termo in termos:
            padrao = r"(^|\W)" + re.escape(termo) + r"($|\W)"

            if re.search(padrao, mensagem):
                return True

        return False
