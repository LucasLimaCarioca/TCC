"""Extração de produtos e quantidades a partir de um catálogo fornecido."""

import re


class OrderParser:
    """Recebe texto normalizado e produtos ativos ordenados por categoria/sabor.

    Não consulta nem modifica produtos; preserva aliases, posições e desempates
    do protótipo. Objetos precisam dos atributos id, nome, categoria e sabor.
    """

    def extrair_itens(self, mensagem, produtos):
        # Procura todos os produtos citados na mensagem.
        # Exemplo aceito: "quero 2 caixas de 10L chocolate e 1 caixa de 5L morango".
        itens_por_produto = {}

        for produto in produtos:
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
        for item in self._extrair_itens_por_categoria_compartilhada(mensagem, produtos):
            produto_id = item["produto"].id

            if produto_id not in itens_por_produto:
                itens_por_produto[produto_id] = item

        # Ordena pela posição em que os produtos apareceram na frase do usuário.
        itens = list(itens_por_produto.values())
        itens.sort(key=lambda item: item["posicao"])

        return itens

    def extrair_produtos(self, mensagem, produtos):
        # Para consulta de preço, a quantidade não importa.
        # Reaproveitamos a extração de pedido porque ela já entende categoria + sabor.
        itens = self.extrair_itens(mensagem, produtos)

        return [
            item["produto"]
            for item in itens
        ]

    def encontrar_produto(self, mensagem, produtos):
        # Disponibilidade mantém a prioridade da ordem do catálogo.

        for produto in produtos:
            termos_produto = self._termos_produto(produto)

            if self._posicao_produto_na_mensagem(mensagem, termos_produto) is not None:
                return produto

        return None

    def _extrair_itens_por_categoria_compartilhada(self, mensagem, produtos):
        # Algumas frases citam a categoria uma vez e depois listam sabores.
        # O trecho analisado vai da categoria encontrada até a próxima categoria citada.
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

    def encontrar_ambiguidade(self, mensagem, produtos):
        # Quando o usuário informa só o sabor, verifica se há mais de uma categoria.
        # Retorna o primeiro sabor ambíguo e suas opções na ordem do catálogo.
        produtos_por_sabor = {}

        for produto in produtos:
            sabor = produto.sabor.lower()

            if sabor in mensagem:
                produtos_por_sabor.setdefault(sabor, []).append(produto)

        for sabor, produtos in produtos_por_sabor.items():
            if len(produtos) <= 1:
                continue

            return sabor, produtos

        return None
