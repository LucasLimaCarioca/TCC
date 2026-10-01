"""Coordenação síncrona do atendimento, independente do framework do agente."""

import json

from app.database import db
from app.dialogue.intent_parser import IntentParser
from app.dialogue.order_parser import OrderParser
from app.dialogue.response_builder import ResponseBuilder
from app.models.contexto_conversa import ContextoConversa
from app.models.produto import Produto
from app.services.venda_service import registrar_venda, registrar_vendas_multiplas


class AtendimentoService:
    """Usa a sessão do app ativo; todo contexto de cliente permanece no banco."""

    def __init__(self):
        self.intent_parser = IntentParser()
        self.order_parser = OrderParser()
        self.responses = ResponseBuilder()

    def responder(self, mensagem, cliente_nome="Cliente Simulado"):
        # O contexto persistido tem prioridade sobre uma nova intenção.
        mensagem_normalizada = self.intent_parser.normalizar(mensagem)

        # Antes de interpretar uma nova intenção, verifica se este cliente já possui
        # um pedido pendente aguardando "sim" ou "não".
        resposta_contextual = self._responder_contexto_pendente(
            mensagem_normalizada,
            cliente_nome
        )

        if resposta_contextual is not None:
            return resposta_contextual

        # Se não há contexto pendente, interpreta a mensagem como uma nova solicitação.
        intencao = self.intent_parser.interpretar(mensagem_normalizada)

        # Cada intenção chama um método específico para manter o fluxo legível.
        if intencao == "saudacao":
            return self.responses.saudacao()

        if intencao == "consultar_sabores":
            return self._responder_sabores()

        if intencao == "consultar_precos":
            return self._responder_precos(mensagem_normalizada)

        if intencao == "consultar_disponibilidade":
            return self._responder_disponibilidade(mensagem_normalizada)

        if intencao == "registrar_venda":
            return self._processar_pedido(
                mensagem_normalizada,
                cliente_nome
            )

        if intencao == "confirmacao_sem_contexto":
            return self.responses.confirmacao_sem_contexto()

        return self.responses.intencao_desconhecida()

    def registrar_venda(
        self,
        produto_nome,
        quantidade,
        cliente_nome="Cliente Simulado"
    ):
        # Localiza o produto por nome e delega a transação ao serviço de vendas.
        produto = Produto.query.filter_by(
            nome=produto_nome
        ).first()

        if produto is None:
            return self.responses.produto_nao_encontrado()

        # A regra de negócio de estoque e persistência fica em venda_service.py.
        sucesso, mensagem, venda = registrar_venda(
            produto.id,
            quantidade,
            cliente_nome=cliente_nome
        )

        if not sucesso:
            return mensagem

        return self.responses.venda_registrada(venda)

    def registrar_pedido(self, itens, cliente_nome="Cliente Simulado"):
        # Registra todos os itens confirmados pelo cliente em uma única operação.
        # "itens" vem do contexto salvo após o agente montar o resumo do pedido.
        sucesso, mensagem, vendas = registrar_vendas_multiplas(
            itens,
            cliente_nome=cliente_nome
        )

        if not sucesso:
            return mensagem

        return self.responses.pedido_registrado(vendas)

    def _buscar_produtos_ativos(self):
        # Consulta centralizada para manter as respostas sempre baseadas no banco.
        return Produto.query.filter_by(
            ativo=True
        ).order_by(
            Produto.categoria,
            Produto.sabor
        ).all()

    def _responder_sabores(self):
        return self.responses.sabores(self._buscar_produtos_ativos())

    def _responder_precos(self, mensagem):
        produtos = self._buscar_produtos_ativos()
        mencionados = self.order_parser.extrair_produtos(mensagem, produtos)
        if 0 < len(mencionados) <= 3:
            return self.responses.precos_produtos(mencionados)
        return self.responses.tabela_precos(produtos)

    def _responder_disponibilidade(self, mensagem):
        produtos = self._buscar_produtos_ativos()
        especifico = self.order_parser.encontrar_produto(mensagem, produtos)
        return self.responses.disponibilidade(produtos, especifico)

    def _processar_pedido(self, mensagem, cliente_nome):
        produtos = self._buscar_produtos_ativos()
        itens = self.order_parser.extrair_itens(mensagem, produtos)
        if not itens:
            ambiguidade = self.order_parser.encontrar_ambiguidade(mensagem, produtos)
            if ambiguidade is not None:
                return self.responses.sabor_ambiguo(*ambiguidade)
            return self.responses.pedido_nao_identificado()

        erro_estoque = self._validar_estoque_itens(itens)
        if erro_estoque is not None:
            return erro_estoque

        self._salvar_contexto_confirmacao(cliente_nome, itens)
        return self.responses.confirmacao_pedido(itens)

    def _validar_estoque_itens(self, itens):
        # A venda revalida o saldo na confirmação, como no protótipo.
        for item in itens:
            produto = item["produto"]
            if produto.quantidade_disponivel < item["quantidade"]:
                return self.responses.estoque_insuficiente(produto)
        return None

    def _buscar_contexto(self, cliente_nome):
        # Busca o contexto pendente daquele cliente.
        # Como cliente_nome é único em ContextoConversa, retorna no máximo um registro.
        return ContextoConversa.query.filter_by(
            cliente_nome=cliente_nome
        ).first()

    def _salvar_contexto_confirmacao(self, cliente_nome, itens):
        # Guarda o pedido pendente para a próxima mensagem do mesmo cliente.
        contexto = self._buscar_contexto(cliente_nome)

        if contexto is None:
            contexto = ContextoConversa(
                cliente_nome=cliente_nome,
                etapa="aguardando_confirmacao"
            )
            db.session.add(contexto)

        contexto.etapa = "aguardando_confirmacao"
        contexto.produto_id = itens[0]["produto"].id
        contexto.quantidade = itens[0]["quantidade"]
        contexto.itens_json = json.dumps([
            {
                "produto_id": item["produto"].id,
                "quantidade": item["quantidade"]
            }
            for item in itens
        ])

        db.session.commit()

    def _limpar_contexto(self, contexto):
        # Remove o pedido ao cancelar ou antes de tentar registrar a confirmação.
        db.session.delete(contexto)
        db.session.commit()

    def _carregar_itens_contexto(self, contexto):
        # Contextos antigos podem ter apenas produto_id/quantidade.
        # O fallback mantém esses registros compatíveis.
        if contexto.itens_json:
            itens_salvos = json.loads(contexto.itens_json)
        else:
            itens_salvos = [
                {
                    "produto_id": contexto.produto_id,
                    "quantidade": contexto.quantidade
                }
            ]

        itens = []

        for item in itens_salvos:
            # Recarrega o produto do banco para usar preço/nome atualizados.
            produto = db.session.get(Produto, item["produto_id"])

            if produto is None:
                continue

            itens.append({
                "produto_id": produto.id,
                "produto": produto,
                "quantidade": item["quantidade"]
            })

        return itens

    def _responder_contexto_pendente(self, mensagem, cliente_nome):
        # Trata mensagens enviadas enquanto existe pedido aguardando confirmação.
        contexto = self._buscar_contexto(cliente_nome)

        if contexto is None:
            return None

        if contexto.etapa != "aguardando_confirmacao":
            return None

        # Permite que o cliente abandone o pedido pendente.
        if self.intent_parser.negativa(mensagem):
            self._limpar_contexto(contexto)
            return self.responses.cancelamento()

        if self.intent_parser.afirmativa(mensagem):
            # Confirmação positiva: carrega os itens salvos no contexto,
            # limpa o contexto e registra o pedido.
            itens = self._carregar_itens_contexto(contexto)
            self._limpar_contexto(contexto)

            return self.registrar_pedido(
                itens,
                cliente_nome=cliente_nome
            )

        if self.intent_parser.interpretar(mensagem) == "registrar_venda":
            # Se o cliente mandar outro pedido antes de confirmar, substituímos o contexto.
            return self._processar_pedido(
                mensagem,
                cliente_nome
            )

        itens = self._carregar_itens_contexto(contexto)

        return self.responses.lembrete(itens)
