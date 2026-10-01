# Fase 2 — Refatoração do Atendimento

## Resultado e escopo

O atendimento agora possui um serviço de aplicação e módulos de diálogo
independentes do transporte do agente. A interface pública de `AtendimentoAgent`
e as respostas do TCC I foram preservadas nos cenários testados.

A entrada nesta fase foi validada com os 160 testes das fases anteriores,
incluindo modelos, migrações e os CHECKs de movimentação positiva e saldo de
produto não negativo. Foram acrescentados 19 testes de caracterização e
executados com sucesso no agente original antes da extração.

## Responsabilidades

| Arquivo | Responsabilidade |
|---|---|
| `app/agents/atendimento_agent.py` | Fachada dos métodos `responder`, `registrar_venda` e `registrar_pedido`, com as mesmas assinaturas e cliente padrão. |
| `app/services/atendimento_service.py` | Coordena consulta ao catálogo, validação inicial de saldo, contexto por cliente e chamada ao serviço de vendas. |
| `app/dialogue/intent_parser.py` | Normalização, prioridade de intenções, afirmação e negação. |
| `app/dialogue/order_parser.py` | Extração de produtos/quantidades, aliases de categoria, categoria compartilhada e identificação de sabor ambíguo. |
| `app/dialogue/response_builder.py` | Montagem de respostas, listas, resumos e totais exibidos. |
| `app/services/venda_service.py` | Continua responsável pela validação final, registro da venda e baixa de saldo na mesma transação; não foi alterado. |

Os interpretadores e o formatador não importam Flask, SQLAlchemy ou SPADE.
Recebem texto e objetos com atributos do catálogo. `OrderParser` espera texto
normalizado e uma lista de produtos ativos ordenada por categoria/sabor; a
ordem é relevante para desempates do protótipo. Não mantém catálogo em cache.

`AtendimentoService` usa SQLAlchemy e exige um contexto de aplicação Flask ativo,
assim como o agente anterior. Suas instâncias não guardam estado de clientes:
os pedidos pendentes permanecem no banco. A confirmação recarrega os produtos
para consultar o preço e saldo atuais. O histórico das mensagens continua sendo
persistido pelas rotas, com o mesmo contrato HTTP.

A integração futura com SPADE poderá chamar o serviço dentro do contexto de
aplicação adequado. Nenhum runtime, gateway, comunicação XMPP ou agente adicional
foi introduzido nesta fase. Não há alteração de schema nem migração a executar.

## Comportamentos preservados

- Textos, totais, quebras de linha e ordenação das respostas cobertas pelos testes.
- Confirmação antes da venda, cancelamento e isolamento por cliente.
- Novo pedido válido substitui o pendente; tentativa inválida mantém o anterior.
- Cancelamento tem prioridade se a mensagem contiver afirmação e negação.
- Contextos antigos sem `itens_json` continuam usando produto/quantidade.
- A quantidade omitida assume uma unidade; aliases e categoria compartilhada
  seguem as regras anteriores.
- Até três produtos recebem preços específicos; mais produtos retornam a tabela.

As limitações já documentadas no baseline continuam presentes: quantidade zero
só é rejeitada ao confirmar; a palavra “sabor” tem prioridade de catálogo;
falha de confirmação por saldo insuficiente apaga o contexto; pedido vazio no
serviço de vendas informa sucesso. A validação individual de itens duplicados
nesse serviço ainda precisa de uma decisão de negócio; o CHECK da Fase 1 impede
que a transação persista saldo negativo, mas não substitui o tratamento da
entrada duplicada. Esta extração não modifica essas regras.

## Validação

Os testes usam somente dados fictícios. Os 19 novos casos de caracterização
foram executados antes e depois da refatoração. Mais 19 casos exercitam diálogo
sem banco/contexto Flask e um caso exercita o serviço diretamente entre
instâncias e sessões distintas, com atualização de preço antes da confirmação.
Os oito cenários obrigatórios do TCC I continuam na suíte de regressão.

Resultado final: **199 testes aprovados**, 39 a mais que o baseline de entrada.
Os 81 avisos são de `datetime.utcnow()` nos modelos legados, agora exercitados
também pelos novos testes. O verificador de privacidade não encontrou caminhos
restritos no índice Git; `git diff --check` passou.

```bash
.venv/bin/python -m pytest -q
python3 scripts/check_private_data.py
```

Para executar a aplicação já inicializada:

```bash
.venv/bin/python run.py
```

Não é necessário reinstalar dependências, migrar o banco ou executar o seed
para esta fase. Não houve leitura ou processamento do CSV histórico; os dados
originais e derivados continuam restritos às pastas ignoradas do projeto.
