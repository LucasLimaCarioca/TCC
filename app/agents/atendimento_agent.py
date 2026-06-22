import json
import re

from app.database import db
from app.models.contexto_conversa import ContextoConversa
from app.models.produto import Produto
from app.services.venda_service import registrar_venda, registrar_vendas_multiplas


class AtendimentoAgent:
    # Protótipo de agente da fase 4.
    # Ele ainda não usa SPADE: simula o comportamento de um agente com métodos bem separados.
    # Fluxo: receber mensagem -> interpretar intenção -> consultar banco -> gerar resposta.
    # Fase 5: adiciona contexto simples, confirmação de compra e tratamento de erros.

    def responder(self, mensagem, cliente_nome="Cliente Simulado"):
        # Ponto de entrada do agente.
        # A rota Flask envia a mensagem do usuário para cá e recebe uma resposta textual.
        mensagem_normalizada = self._normalizar_mensagem(mensagem)

        # Antes de interpretar uma nova intenção, verifica se este cliente já possui
        # um pedido pendente aguardando "sim" ou "não".
        resposta_contextual = self._responder_contexto_pendente(
            mensagem_normalizada,
            cliente_nome
        )

        if resposta_contextual is not None:
            return resposta_contextual

        # Se não há contexto pendente, interpreta a mensagem como uma nova solicitação.
        intencao = self._interpretar_intencao(mensagem_normalizada)

        # Cada intenção chama um método específico para manter o fluxo legível.
        if intencao == "saudacao":
            return self._responder_saudacao()

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
            return (
                "Não encontrei nenhum pedido aguardando confirmação.\n"
                "Para iniciar um pedido, envie algo como: quero 2 caixas de 10L chocolate."
            )

        return self._responder_intencao_desconhecida()

    def registrar_venda(
        self,
        produto_nome,
        quantidade,
        cliente_nome="Cliente Simulado"
    ):
        # Método público mantido para outras partes do sistema poderem pedir uma venda ao agente.
        # Ele recebe o nome do produto, encontra o registro no banco e delega a venda ao service.
        produto = Produto.query.filter_by(
            nome=produto_nome
        ).first()

        if produto is None:
            return "Produto não encontrado."

        # A regra de negócio de estoque e persistência fica em venda_service.py.
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

    def registrar_pedido(self, itens, cliente_nome="Cliente Simulado"):
        # Registra todos os itens confirmados pelo cliente em uma única operação.
        # "itens" vem do contexto salvo após o agente montar o resumo do pedido.
        sucesso, mensagem, vendas = registrar_vendas_multiplas(
            itens,
            cliente_nome=cliente_nome
        )

        if not sucesso:
            return mensagem

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

    def _normalizar_mensagem(self, mensagem):
        # Normalizar evita repetir lower/strip em cada regra de intenção.
        return mensagem.lower().strip()

    def _interpretar_intencao(self, mensagem):
        # Interpretação simples por palavras-chave.
        # Na fase 4, isso já é suficiente para demonstrar o conceito de agente.
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

        if self._mensagem_afirmativa(mensagem):
            # "sim" só confirma pedido se houver contexto pendente.
            # Sem contexto, o agente explica que não há pedido para confirmar.
            return "confirmacao_sem_contexto"

        return "desconhecida"

    def _buscar_produtos_ativos(self):
        # Consulta centralizada para manter as respostas sempre baseadas no banco.
        return Produto.query.filter_by(
            ativo=True
        ).order_by(
            Produto.categoria,
            Produto.sabor
        ).all()

    def _responder_saudacao(self):
        return (
            "Olá! Sou o agente de atendimento da sorveteria.\n"
            "Posso informar sabores, preços, disponibilidade ou registrar um pedido."
        )

    def _responder_sabores(self):
        # Lista os sabores agrupados por categoria/tamanho.
        # Isso é importante porque o mesmo sabor pode existir em caixa de 10L, 5L etc.
        produtos = self._buscar_produtos_ativos()

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

    def _responder_precos(self, mensagem):
        # Quando a pergunta cita produtos específicos, responde só esses valores.
        # Se a pergunta citar muitos produtos, a tabela completa fica mais legível.
        produtos_mencionados = self._extrair_produtos_mencionados(mensagem)

        if 0 < len(produtos_mencionados) <= 3:
            return self._formatar_precos_produtos(produtos_mencionados)

        # Responde com a tabela de preços agrupada por categoria.
        produtos = self._buscar_produtos_ativos()

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

    def _responder_disponibilidade(self, mensagem):
        # Se a pergunta cita categoria + sabor, responde só aquele produto.
        # Exemplo: "Tem caixa de 10L de chocolate?"
        produto_especifico = self._encontrar_produto_na_mensagem(mensagem)

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
        produtos = self._buscar_produtos_ativos()

        if not produtos:
            return "No momento não há produtos ativos para consulta."

        resposta = "Disponibilidade atual:\n"

        for produto in produtos:
            resposta += (
                f"{produto.categoria} - {produto.sabor}: "
                f"{produto.quantidade_disponivel} unidades\n"
            )

        return resposta

    def _processar_pedido(self, mensagem, cliente_nome):
        # Tenta transformar a frase do usuário em uma lista de itens de pedido.
        # Exemplo: "quero 2 caixas de 10L chocolate e 1 caixa de 5L morango".
        itens = self._extrair_itens_pedido(mensagem)

        if not itens:
            # Se o cliente disser apenas "chocolate", pode haver ambiguidade,
            # pois existem produtos de chocolate em categorias diferentes.
            resposta_ambiguidade = self._responder_sabor_sem_categoria(mensagem)

            if resposta_ambiguidade is not None:
                return resposta_ambiguidade

            return (
                "Não consegui identificar os produtos do pedido.\n"
                "Exemplo: quero 2 caixas de 10L chocolate e 1 caixa de sundae morango."
            )

        erro_estoque = self._validar_estoque_itens(itens)

        if erro_estoque is not None:
            return erro_estoque

        # O agente não registra a venda imediatamente.
        # Primeiro salva o pedido como contexto pendente e pede confirmação.
        self._salvar_contexto_confirmacao(
            cliente_nome,
            itens
        )

        return self._formatar_confirmacao_pedido(itens)

    def _responder_intencao_desconhecida(self):
        return (
            "Desculpe, não entendi sua solicitação.\n"
            "Você pode perguntar por sabores, preços, disponibilidade ou fazer um pedido."
        )

    def _encontrar_produto_na_mensagem(self, mensagem):
        # Método auxiliar mantido para fluxos antigos/simples.
        # Hoje o fluxo principal de pedidos usa _extrair_itens_pedido.
        produtos = self._buscar_produtos_ativos()

        for produto in produtos:
            termos_produto = self._termos_produto(produto)

            if self._posicao_produto_na_mensagem(mensagem, termos_produto) is not None:
                return produto

        return None

    def _extrair_quantidade(self, mensagem):
        # Retorna None quando a mensagem não informar quantidade.
        # O pedido assume 1 unidade, mas a resposta deixa isso claro na confirmação.
        resultado = re.search(r"\d+", mensagem)

        if resultado is None:
            return None

        return int(resultado.group())

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
        # Remove o pedido pendente depois de confirmar, cancelar ou substituir.
        db.session.delete(contexto)
        db.session.commit()

    def _responder_contexto_pendente(self, mensagem, cliente_nome):
        # Trata mensagens enviadas enquanto existe pedido aguardando confirmação.
        contexto = self._buscar_contexto(cliente_nome)

        if contexto is None:
            return None

        if contexto.etapa != "aguardando_confirmacao":
            return None

        # Permite que o cliente abandone o pedido pendente.
        if self._mensagem_negativa(mensagem):
            self._limpar_contexto(contexto)
            return "Pedido cancelado. Posso ajudar com outra coisa?"

        if self._mensagem_afirmativa(mensagem):
            # Confirmação positiva: carrega os itens salvos no contexto,
            # limpa o contexto e registra o pedido.
            itens = self._carregar_itens_contexto(contexto)
            self._limpar_contexto(contexto)

            return self.registrar_pedido(
                itens,
                cliente_nome=cliente_nome
            )

        if self._interpretar_intencao(mensagem) == "registrar_venda":
            # Se o cliente mandar outro pedido antes de confirmar, substituímos o contexto.
            return self._processar_pedido(
                mensagem,
                cliente_nome
            )

        itens = self._carregar_itens_contexto(contexto)

        # Qualquer outra mensagem mantém o pedido pendente e orienta o cliente.
        return (
            "Ainda tenho um pedido aguardando confirmação:\n"
            f"{self._formatar_resumo_itens(itens)}"
            "Responda sim para confirmar ou não para cancelar."
        )

    def _mensagem_afirmativa(self, mensagem):
        return self._contem_termo(
            mensagem,
            ["sim", "confirmo", "confirmar", "pode ser", "ok", "certo"]
        )

    def _mensagem_negativa(self, mensagem):
        return self._contem_termo(
            mensagem,
            ["não", "nao", "cancelar", "cancela", "desistir"]
        )

    def _extrair_itens_pedido(self, mensagem):
        # Procura todos os produtos citados na mensagem.
        # Exemplo aceito: "quero 2 caixas de 10L chocolate e 1 caixa de 5L morango".
        itens_por_produto = {}

        for produto in self._buscar_produtos_ativos():
            # Para cada produto cadastrado, gera termos possíveis para encontrá-lo na frase.
            termos_produto = self._termos_produto(produto)
            posicao = self._posicao_produto_na_mensagem(
                mensagem,
                termos_produto
            )

            if posicao is None:
                continue

            # A quantidade é associada ao número mais próximo antes do produto.
            quantidade = self._extrair_quantidade_antes_do_produto(
                mensagem,
                posicao
            )

            itens_por_produto[produto.id] = {
                "produto": produto,
                "quantidade": quantidade,
                "posicao": posicao
            }

        # Complementa a extração para frases onde uma categoria vale para vários sabores.
        # Exemplo: "quero 1 caixa de 10L de chocolate, morango e baunilha".
        for item in self._extrair_itens_por_categoria_compartilhada(mensagem):
            produto_id = item["produto"].id

            if produto_id not in itens_por_produto:
                itens_por_produto[produto_id] = item

        # Ordena pela posição em que os produtos apareceram na frase do usuário.
        itens = list(itens_por_produto.values())
        itens.sort(key=lambda item: item["posicao"])

        return itens

    def _extrair_produtos_mencionados(self, mensagem):
        # Para consulta de preço, a quantidade não importa.
        # Reaproveitamos a extração de pedido porque ela já entende categoria + sabor.
        itens = self._extrair_itens_pedido(mensagem)

        return [
            item["produto"]
            for item in itens
        ]

    def _formatar_precos_produtos(self, produtos):
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

    def _extrair_itens_por_categoria_compartilhada(self, mensagem):
        # Algumas frases citam a categoria uma vez e depois listam sabores.
        # O trecho analisado vai da categoria encontrada até a próxima categoria citada.
        produtos = self._buscar_produtos_ativos()
        categorias = sorted({
            produto.categoria.lower()
            for produto in produtos
        })
        ocorrencias_categoria = []

        for categoria in categorias:
            for termo in self._termos_categoria(categoria):
                posicao = mensagem.find(termo)

                if posicao >= 0:
                    ocorrencias_categoria.append({
                        "categoria": categoria,
                        "termo": termo,
                        "posicao": posicao
                    })

        itens = []

        for ocorrencia in ocorrencias_categoria:
            posicao_categoria = ocorrencia["posicao"]
            fim_trecho = len(mensagem)

            for outra_ocorrencia in ocorrencias_categoria:
                outra_posicao = outra_ocorrencia["posicao"]

                if posicao_categoria < outra_posicao < fim_trecho:
                    fim_trecho = outra_posicao

            trecho_categoria = mensagem[posicao_categoria:fim_trecho]
            quantidade = self._extrair_quantidade_antes_do_produto(
                mensagem,
                posicao_categoria
            )

            for produto in produtos:
                if produto.categoria.lower() != ocorrencia["categoria"]:
                    continue

                if produto.sabor.lower() not in trecho_categoria:
                    continue

                itens.append({
                    "produto": produto,
                    "quantidade": quantidade,
                    "posicao": posicao_categoria
                })

        return itens

    def _posicao_produto_na_mensagem(self, mensagem, termos):
        # Retorna a primeira posição em que algum termo do produto aparece na mensagem.
        posicoes = [
            mensagem.find(termo)
            for termo in termos
            if mensagem.find(termo) >= 0
        ]

        if not posicoes:
            return None

        return min(posicoes)

    def _extrair_quantidade_antes_do_produto(self, mensagem, posicao_produto):
        # Procura um número imediatamente antes do produto.
        # Se não encontrar, assume 1 unidade.
        trecho_anterior = mensagem[:posicao_produto].strip()
        resultado = re.search(r"(\d+)\D*$", trecho_anterior)

        if resultado is None:
            return 1

        return int(resultado.group(1))

    def _validar_estoque_itens(self, itens):
        # Valida estoque antes de salvar o contexto de confirmação.
        # O service também valida novamente antes de registrar a venda.
        for item in itens:
            produto = item["produto"]
            quantidade = item["quantidade"]

            if produto.quantidade_disponivel < quantidade:
                # Estoque zerado precisa de uma mensagem diferente:
                # não existe quantidade menor possível para aquele produto.
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

        return None

    def _formatar_confirmacao_pedido(self, itens):
        # Mensagem enviada antes de registrar a venda.
        # O usuário precisa confirmar com "sim".
        return (
            "Encontrei este pedido:\n"
            f"{self._formatar_resumo_itens(itens)}"
            "Confirma a compra? Responda sim ou não."
        )

    def _formatar_resumo_itens(self, itens):
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

    def _termos_produto(self, produto):
        # Um sabor pode existir em várias categorias.
        # Por isso o agente só casa pedidos com categoria + sabor, ou com nome completo.
        categoria = produto.categoria.lower()
        sabor = produto.sabor.lower()
        nome = produto.nome.lower()
        termos = [
            nome,
            f"{categoria} {sabor}",
            f"{categoria} de {sabor}"
        ]

        for alias in self._aliases_categoria(categoria):
            termos.append(f"{alias} {sabor}")
            termos.append(f"{alias} de {sabor}")

        return termos

    def _termos_categoria(self, categoria):
        # Centraliza categoria oficial + apelidos para reaproveitar em consultas
        # e na extração de pedidos com vários sabores da mesma categoria.
        return [
            categoria,
            *self._aliases_categoria(categoria)
        ]

    def _aliases_categoria(self, categoria):
        # Apelidos para aceitar formas naturais de escrever a categoria.
        # Exemplo: "10l chocolate" identifica "caixa de 10L - chocolate".
        aliases = {
            "caixa de 10l": ["10l", "10 litros", "caixa 10l", "caixa de 10 litros", "caixas de 10l"],
            "caixa de 5l": ["5l", "5 litros", "caixa 5l", "caixa de 5 litros", "caixas de 5l"],
            "caixa de sundae": ["sundae", "caixa sundae", "caixas de sundae"],
            "caixa de picole": ["picole", "picolé", "caixa picole", "caixa picolé", "caixas de picole"]
        }

        return aliases.get(categoria, [])

    def _responder_sabor_sem_categoria(self, mensagem):
        # Quando o usuário informa só o sabor, verifica se há mais de uma categoria.
        # Se houver ambiguidade, o agente pede para informar categoria/tamanho.
        produtos_por_sabor = {}

        for produto in self._buscar_produtos_ativos():
            sabor = produto.sabor.lower()

            if sabor in mensagem:
                produtos_por_sabor.setdefault(sabor, []).append(produto)

        for sabor, produtos in produtos_por_sabor.items():
            if len(produtos) <= 1:
                continue

            categorias = ", ".join(
                produto.categoria
                for produto in produtos
            )

            return (
                f"Encontrei o sabor {sabor} em mais de uma categoria: {categorias}.\n"
                "Informe também a categoria/tamanho.\n"
                f"Exemplo: quero 2 caixas de 10L {sabor}."
            )

        return None
