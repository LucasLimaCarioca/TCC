# Fase 9 — Interface web final

A aplicação Flask reúne Atendimento, Estoque e Previsões na mesma navegação.
A tela de vendas existente continua disponível. Atendimento, movimentações,
mínimos e alertas mantêm seus fluxos; a nova área `/previsoes` permite consultar
a publicação do Agente de Previsão e solicitar seu recálculo em segundo plano.
Não há outra aplicação web, novos agentes, receitas/BOM ou integração de LLM.

## Consulta das previsões

A tela apresenta a visão geral ou um código de produto com histórico elegível,
o agrupamento diário/semanal/mensal e o intervalo de datas. Também informa o
total previsto, os valores por período, um gráfico, a data de corte, a cobertura
e a data de publicação. Uma referência antiga permanece consultável com aviso
de desatualização; a interface não muda silenciosamente as datas do artefato.

O seletor inclui todos os códigos da execução ativa, mesmo sem vínculo ao
catálogo. O código textual preserva zeros à esquerda; nome do catálogo aparece
somente quando existe associação explícita por `Produto.codigo_externo`.
Grupo e categoria vêm dos metadados preparados da execução. A consulta não cria
produtos nem associa códigos por semelhança de nomes ou equivalência numérica.
Sem descrição cadastrada, o rótulo indica essa ausência e identifica a categoria
como categoria, sem usá-la como nome comercial completo.

A visão geral soma todos os códigos elegíveis da publicação. Um produto sem
previsão não representa demanda zero. Esse total agrega quantidades comerciais;
não converte produtos para litros, peso, faturamento ou consumo de insumos.

O intervalo deve estar ordenado e inteiramente dentro da cobertura publicada.
Sem datas, a consulta usa toda a cobertura. A aplicação primeiro seleciona os
valores diários e depois os agrega com `Decimal`. Semanas vão de segunda a
domingo; meses seguem o calendário. A resposta preserva os decimais textuais,
a cobertura efetiva e os períodos parciais.

Após a validação manual, gráfico e tabela apresentam estimativas inteiras.
Cada período usa HALF_UP: fração menor que 0,5 fica no inteiro inferior e fração
igual ou maior que 0,5 sobe para o superior. A operação usa o decimal textual,
sem conversão intermediária para ponto flutuante. O total exibido soma os
inteiros das linhas, e os pontos do gráfico usam esses mesmos valores.

Arredondar cada período pode produzir um total diferente de arredondar a soma
decimal. Também pode haver diferença entre agrupamentos ou em relação à soma de
produtos arredondados separadamente. Essas diferenças são explicadas na tela e
não alteram a publicação, os artefatos, as métricas ou os alertas preventivos.
A conversão numérica para coordenadas SVG participa somente do desenho.

Consultas diárias com até sete dias mostram todas as datas no eixo X. Intervalos
maiores usam espaçamento adaptativo de rótulos, sem remover períodos da tabela.
Em telas estreitas, o gráfico permite rolagem horizontal para preservar a leitura.

## Modelos e avaliação

O painel mostra o modelo selecionado, o histórico do modelo publicado, a janela de
teste e as métricas disponíveis na última avaliação da publicação. A seleção
continua baseada em validação temporal. O teste final compara o modelo com
`naive` e `seasonal_naive`; não escolhe novamente o vencedor.

**Histórico do modelo publicado** indica o intervalo usado no ajuste final do
modelo, após a seleção e a avaliação temporal, até a data de corte. As métricas
de validação e de teste foram obtidas nas separações anteriores dessa avaliação,
antes desse ajuste final. Esse histórico mais amplo não significa que os dias
do teste foram usados para treinar o modelo que produziu as métricas de teste.

- MAE e RMSE expressam erro em unidades.
- WAPE e sMAPE expressam erro em porcentagem.
- Valores menores indicam menor erro no período avaliado.

Na visão geral, os resultados são apresentados por produto: não se calcula uma
média de métricas nem se inventa uma métrica de previsão agregada. Um fallback
sem avaliação aparece como tal, com seu motivo. Métrica ausente ou WAPE não
definida não é exibida como zero. Selecionar um modelo não significa que ele
superou os dois baselines no teste final; a comparação conserva esse resultado.

O intervalo escolhido para visualizar a previsão não altera a janela nem
recalcula as métricas históricas. Elas pertencem à avaliação que gerou a
publicação, não aos dias futuros selecionados no filtro.

## Comunicação e componentes

```text
Navegador → Rotas Flask → AgentGateway
                                      |
                                 EstoqueAgent
                                      |
                                  XMPP local
                                      |
                                 PrevisaoAgent
                                      |
                         PrevisaoService / painel
                                      |
                       snapshot SQLite + JSON privado
```

A nova consulta `previsao.painel` segue a mesma topologia Estoque → Previsão e
o mesmo contrato de mensagens com `performative`, `ontology`, `thread` e JSON
validado. O contrato `previsao.consulta`, usado para avaliar risco, permanece
separado. A consulta ao painel não cria, modifica ou resolve alertas.

O serviço obtém uma execução ativa e confere o manifesto e os JSON consumidos.
Previsão, modelos, metadados e avaliação retornam a partir dessa mesma
publicação. Não há leitura do CSV original nem desserialização de pickle para
atender à consulta. A visão do painel não altera saldos, vendas, histórico,
associações do catálogo ou publicação ativa.

| Componente | Responsabilidade |
|---|---|
| `app/routes/previsao_routes.py` | Tela, validação HTTP, acesso ao gateway, tradução de falhas e acompanhamento do recálculo. |
| `app/services/previsao_painel_service.py` | Snapshot, seleção por código, recorte diário, agregação e métricas disponíveis. |
| `app/agents/protocol.py` | Contrato explícito da consulta `previsao.painel`. |
| `app/templates/previsoes.html` | Filtros, gráfico, tabela, avaliação e atualização. |
| `app/static/js/previsoes.js` | Consulta, renderização SVG local e acompanhamento da tarefa. |
| `app/templates/base.html` e `app/static/css/style.css` | Navegação comum e adaptação das telas a diferentes larguras. |

## API

| Método e rota | Efeito |
|---|---|
| `GET /previsoes` | Renderiza a estrutura da tela, sem iniciar treinamento. |
| `GET /api/previsoes` | Consulta somente leitura do painel pelo agente. |
| `POST /api/previsoes/atualizar` | Solicita recálculo e retorna HTTP 202 com a tarefa aceita. |
| `GET /api/previsoes/tarefas/<uuid>` | Consulta o estado da tarefa no agente atual. |

O GET do painel aceita `codigo_produto`, `granularidade`, `data_inicio` e
`data_fim`. Omitir `codigo_produto` seleciona a visão geral. A granularidade
padrão é `diaria`; as outras opções são `semanal` e `mensal`. Datas usam
`YYYY-MM-DD`. Valores inválidos ou intervalos fora da cobertura retornam erro
tratado, sem completar dias ausentes com zeros.

O POST recebe exclusivamente a data de corte e o horizonte em dias de
calendário. Exemplo fictício de formato:

```json
{"data_corte": "2025-01-31", "horizonte_dias": 28}
```

A data precisa corresponder a um dia completo anterior a hoje em
`America/Manaus` e ser compatível com a revisão aprovada. O horizonte é um
inteiro entre 1 e 366. A aceitação da tarefa não equivale a uma atualização
concluída: o navegador acompanha `em_execucao`, `concluido` ou `falhou`.

## Atualização e configuração

Use o launcher existente e configure as credenciais XMPP conforme o
[guia da Fase 3](FASE_3_SPADE_XMPP.md). Manifesto, revisão e início do histórico
seguem o [guia da Fase 7](FASE_7_AGENTE_PREVISAO.md).

`FORECAST_REVIEW` deve apontar para uma revisão privada aprovada para habilitar
o recálculo. A configuração pode vir do ambiente ou do Flask; em testes, apenas
a configuração explícita da aplicação é considerada. Não aponte a variável para
o CSV bruto. O launcher não carrega `.env.example` automaticamente.

Quando há publicação, a interface sugere manter sua data de corte. O limite
informado considera a referência da revisão e o último dia completo. Avançar o
relógio não atualiza a fonte histórica nem autoriza prolongar o corte. A
indicação da tela não substitui a validação completa executada pelo trabalhador.

O POST atravessa o gateway e o XMPP; o agente agenda o trabalho já implementado
na Fase 7. O motor roda em segundo plano, fora da thread HTTP e da Inbox que
recebe as mensagens. Apenas um recálculo é permitido por vez. A previsão atual
permanece disponível até a publicação transacional da nova execução. Se o
trabalho falhar, a publicação anterior é preservada e o erro aparece no
acompanhamento, sem mensagem de sucesso falsa.

Os estados das tarefas pertencem à execução atual do agente e não são
persistidos. Após reinício, uma tarefa pode não ser encontrada. A interface
permite retomar uma consulta de acompanhamento interrompida sem reenviar o
recálculo. Não há treinamento periódico nem nova migration nesta fase.

Sem modelo, sem runtime ou diante de falhas dos artefatos, a tela apresenta o
estado indisponível. Não inventa previsões, métricas ou demanda zero. O
Atendimento e o Controle de Estoque continuam usando seus fluxos anteriores.

## Privacidade

O gráfico é SVG gerado localmente; não há CDN, fonte externa ou envio de dados
para serviços de terceiros. A interface não grava previsões em `localStorage`
nem produz exportações fora de `data/`. Respostas da área de previsões usam
`Cache-Control: no-store`; códigos históricos de consultas são redigidos no
log de acesso do launcher, e payloads XMPP continuam omitidos da auditoria.

Banco, CSV e derivados permanecem restritos a `data/`. Capturas, gráficos ou
relatórios que contenham resultados reais também são dados restritos. Os
testes desta fase usam exclusivamente produtos, métricas e previsões fictícios.

## Validação

O escopo de regressão inclui consultas gerais e por código sem catálogo,
datas/granularidades, precisão e períodos parciais, métricas e fallback,
indisponibilidade, atualização e falha de tarefas, privacidade das respostas,
ausência de efeitos colaterais em GET e transporte SPADE/XMPP real. Os oito
casos de Atendimento do TCC I e os fluxos de Estoque continuam na suíte.

Os **67 testes HTTP** desta fase passaram, incluindo o fluxo com SPADE/XMPP
real. Nesse fluxo, o trabalhador foi mantido em execução enquanto outra
consulta confirmou a disponibilidade da publicação anterior; o recálculo
ocorreu fora da thread HTTP.

A revisão no navegador usou somente dados fictícios, em desktop e em largura
de 390 pixels. Foram conferidos seleção de código sem catálogo, agrupamento
semanal, intervalo de datas, tabela, gráfico, avaliação e fallback. A ação de
atualização concluiu a tarefa e apresentou a nova cobertura. O console
permaneceu sem erros.

Uma conferência local adicional consultou a publicação real com o SQLite
aberto somente para leitura e validou o retorno do serviço, o contrato e as
somas decimais. A evidência ficou exclusivamente em `data/reports/`, sem copiar
códigos, previsões ou métricas reais para esta documentação.

Validação final em 23/10/2026: **1.082 testes aprovados** na suíte completa e
**61 aprovados** na seleção adicional com `--spade`, executadas em sequência.
As verificações de dependências, JavaScript de Estoque e Previsões, proteção
dos dados e `git diff --check` também passaram. Os avisos restantes são
depreciações já existentes das dependências. O workflow de CI passa a verificar
também a sintaxe do novo JavaScript.

Comandos de verificação:

```bash
python -m pip check
node --check app/static/js/estoque.js
node --check app/static/js/previsoes.js
node --check app/static/js/notificacoes_estoque.js
node --test tests/js/*.test.cjs
python -m pytest -q
python -m pytest -q --spade \
  tests/test_regressao_tcc_i.py \
  tests/test_atendimento_contexto.py \
  tests/test_atendimento_caracterizacao.py \
  tests/test_estoque_http.py \
  tests/test_correcoes_estoque.py \
  tests/test_estoque_service.py::test_resposta_perdida_mantem_contexto_para_repeticao_segura
python3 scripts/check_private_data.py
git diff --check
```

A Fase 10 e seu protocolo de validação experimental são descritos no
[guia de validação experimental](FASE_10_VALIDACAO_EXPERIMENTAL.md). As correções
posteriores desta interface e seus limites estão no
[registro da validação manual](CORRECOES_VALIDACAO_MANUAL.md).
