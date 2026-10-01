"""Regras de intenção e confirmação preservadas do TCC I."""

import re


class IntentParser:
    """Interpreta texto normalizado, sem estado de conversa."""

    def normalizar(self, mensagem):
        # Normalizar evita repetir lower/strip em cada regra de intenção.
        return mensagem.lower().strip()

    def interpretar(self, mensagem):
        # Interpretação simples por palavras-chave.
        if self._contem_termo(mensagem, ["oi", "ola", "olá", "bom dia", "boa tarde"]):
            return "saudacao"

        if self._mensagem_consulta_catalogo(mensagem):
            return "consultar_sabores"

        if any(palavra in mensagem for palavra in ["preço", "preco", "valor", "quanto custa", "tabela"]):
            return "consultar_precos"

        if any(palavra in mensagem for palavra in ["dispon", "tem ", "estoque"]):
            return "consultar_disponibilidade"

        if any(palavra in mensagem for palavra in ["comprar", "quero", "pedido", "levar"]):
            return "registrar_venda"

        if self.afirmativa(mensagem):
            # "sim" só confirma pedido se houver contexto pendente.
            # Sem contexto, o agente explica que não há pedido para confirmar.
            return "confirmacao_sem_contexto"

        return "desconhecida"

    def _contem_termo(self, mensagem, termos):
        # Procura termos como palavras/expressões completas.
        # Isso evita classificar "chocolate" como saudação por conter "ola".
        for termo in termos:
            padrao = r"(^|\W)" + re.escape(termo) + r"($|\W)"

            if re.search(padrao, mensagem):
                return True

        return False

    def _mensagem_consulta_catalogo(self, mensagem):
        # "Produto" pode aparecer em dois contextos:
        # pergunta de catálogo ("quais produtos tem?") ou pedido ("quero comprar").
        # Aqui tratamos somente a pergunta de catálogo como consulta de sabores/produtos.
        termos_catalogo = [
            "sabores",
            "sabor",
            "opcoes",
            "opções"
        ]

        if any(palavra in mensagem for palavra in termos_catalogo):
            return True

        pergunta_catalogo = any(
            termo in mensagem
            for termo in ["quais", "qual", "lista", "listar", "tem"]
        )
        cita_produto = any(
            termo in mensagem
            for termo in ["produto", "produtos"]
        )

        return pergunta_catalogo and cita_produto

    def afirmativa(self, mensagem):
        return self._contem_termo(
            mensagem,
            ["sim", "confirmo", "confirmar", "pode ser", "ok", "certo"]
        )

    def negativa(self, mensagem):
        return self._contem_termo(
            mensagem,
            ["não", "nao", "cancelar", "cancela", "desistir"]
        )
