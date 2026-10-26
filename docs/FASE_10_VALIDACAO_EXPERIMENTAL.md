# Fase 10 — Validação experimental do TCC II

A Fase 10 acrescenta um protocolo local para produzir evidências verificáveis
do sistema implementado. A execução reúne cenários controlados de Atendimento,
Estoque e comunicação SPADE/XMPP com a avaliação temporal já registrada nos
artefatos de previsão. Não modifica o motor de previsão, o esquema do banco ou
a topologia dos três agentes. Receitas/BOM e integração de LLM continuam fora
desta fase.

O experimento funcional usa somente dados fictícios e um SQLite próprio.
A preparação desse ambiente calcula e publica nele uma previsão de referência
a partir de série fictícia curta, para exercitar o fluxo dos agentes.
A parte histórica consulta manifestos publicados em modo somente leitura.
Ela não treina modelos, ativa publicações, movimenta estoque ou altera o banco
operacional em `data/artifacts/sorvetes.db`.

## Executar o protocolo

Use o Python do ambiente virtual com as dependências fixadas instaladas:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m scripts.validate_tccii --help
```

Para executar os cenários fictícios e coletar a avaliação de uma publicação
existente, substitua `<id>` pelo identificador do manifesto privado:

```bash
python -m scripts.validate_tccii \
  --manifest 'data/artifacts/previsao-<id>/manifest.json'
```

`--manifest` pode ser repetido para coletar até dez publicações existentes.
Isso organiza evidências de execuções já produzidas; não cria automaticamente
um novo backtesting nem torna origens sobrepostas independentes. Informe
explicitamente os manifestos que fazem parte do protocolo, sem escolher a
publicação a partir de seu desempenho no teste final.

Uma publicação da origem temporal já aprovada na Fase 6 pode fornecer a
evidência formal de previsão desta primeira versão. Não é necessário inventar
novos cortes, treinar novamente ou alterar o período aprovado para executar
a coleta. Os limites dessa origem devem acompanhar os resultados.

Sem artefatos reais, execute somente o experimento funcional:

```bash
python -m scripts.validate_tccii --functional-only
```

Esse modo também permite verificar a ferramenta em CI com dados inteiramente
fictícios. Ele não produz avaliação histórica real e não deve ser apresentado
como substituto da comparação temporal dos modelos.

O runtime inicia três agentes SPADE e um servidor XMPP em loopback, encerrados
ao final. A execução precisa poder abrir sockets locais. Não exige uma conta
XMPP externa ou credenciais do ambiente de produção. Execute as validações em
sequência no mesmo computador para evitar disputa de portas e de recursos.

O código de saída é `0` para estado técnico `aprovado` e `2` para `falhou` ou
falha controlada. Esse estado indica que o protocolo executou seus critérios,
não que um modelo superou baselines ou melhorou a operação da fábrica.
O console informa apenas status e caminhos privados; códigos históricos,
quantidades, métricas, hashes e conteúdo das fontes permanecem nos relatórios.

O campo `phase10_evidence_complete` no JSON distingue o pacote integral da
execução somente funcional. Ele permanece falso com `--functional-only` e
quando não há avaliação temporal de teste disponível, mesmo que o procedimento
técnico tenha terminado sem falha. Publicações compostas apenas de fallbacks
sem teste não fornecem a comparação quantitativa exigida para fechar a fase.

## Evidências geradas

Cada execução cria uma pasta nova, sem sobrescrever evidências anteriores:

```text
data/reports/validacao-tccii-<uuid>/
  relatorio.json
  resumo.md
  cenarios.csv
  metricas.csv
  comparacoes.csv
  mensagens.csv
  tempos.csv

data/artifacts/validacao-tccii-<uuid>/
  ... banco e recursos fictícios do experimento funcional ...
```

| Arquivo | Uso |
|---|---|
| `relatorio.json` | Evidência estruturada do protocolo, cenários, comunicação e publicações examinadas. |
| `resumo.md` | Leitura local dos resultados, comparações e limites. |
| `cenarios.csv` | Quadro dos cenários controlados e seus estados. |
| `metricas.csv` | Avaliações por código, modelo e janela temporal, com baselines e métricas disponíveis. |
| `comparacoes.csv` | Comparações com cada baseline, por fase e por MAE, RMSE, WAPE e sMAPE. |
| `mensagens.csv` | Contagens de transporte por ontology, performative e envio/recebimento no experimento fictício, sem payload. |
| `tempos.csv` | Duração observada por caso completo, com uma amostra por caso e sem percentis. |

O identificador do experimento diferencia cada coleta; o identificador da
publicação identifica a previsão que foi avaliada. Executar novamente a
coleta não altera os valores históricos medidos pelo motor. Datas, duração,
UUIDs e tempos de resposta de uma nova execução podem mudar.

Ao importar as tabelas CSV em uma planilha, defina a coluna de código como
texto para preservar zeros à esquerda. O JSON conserva a identidade textual
original e o significado dos campos ausentes.
Campos vazios de métricas nos CSVs indicam ausência ou indefinição, conforme
os campos de estado; eles não representam erro zero. O JSON conserva os
detalhes e os hashes das outras seis exportações. Ele é gravado por último
como identificação do pacote concluído.

## Atendimento e Estoque em cenários controlados

Os oito casos de regressão do TCC I continuam sendo o ponto de partida:

1. Consulta de produtos.
2. Consulta de disponibilidade de produto específico.
3. Pedido com múltiplos produtos e confirmação.
4. Pedido com categoria compartilhada e vários sabores.
5. Produto sem estoque.
6. Quantidade superior ao estoque.
7. Consulta de preço de produto específico.
8. Consulta de preço de múltiplos produtos.

O experimento verifica a integração com a autoridade de Estoque por XMPP.
As operações controladas cobrem entrada, saída, ajuste pelo saldo final,
venda, saldo zerado, rejeição de estoque insuficiente e estoque mínimo.
Venda confirmada deve produzir a baixa correspondente; repetição da mesma
operação deve recuperar o recibo sem duplicar venda ou movimentação.

A matriz reúne oito casos de Atendimento pela API HTTP e quatorze de Estoque
pelo gateway: entrada, saída, ajuste, venda, insuficiência, saldo zerado,
rejeição de venda sem saldo, mínimo, recuperação do saldo, deduplicação,
matéria-prima, alerta preventivo, horizontes conflitantes e igualdade no limite
preventivo. A preparação, a saúde do runtime, as quatro direções de comunicação,
o encerramento e a auditoria também possuem verificações explícitas.
Ao todo, são 31 verificações e 22 durações de casos. A previsão utilizada pelo
cenário preventivo é um baseline calculado sobre série curta fictícia, para
verificar o fluxo funcional; ela não é a avaliação do histórico real.

Para o alerta imediato, saldo igual ou inferior ao mínimo é condição de
alerta. Para o preventivo, a condição é saldo projetado estritamente inferior
ao mínimo. A avaliação persistente usa somente
`PREDICTIVE_ALERT_HORIZON_DAYS`; consultas de risco com outro horizonte são
somente leitura. A validação conserva a regressão que impede uma avaliação
com horizonte divergente de encerrar indevidamente um alerta existente.

Esses casos verificam regras e integração do software em condições conhecidas.
Não demonstram, por si só, redução de faltas, aumento de vendas ou melhoria da
operação da fábrica.

## Evidências de comunicação

A coleta verifica os três agentes em execução e utiliza a topologia existente:
Atendimento ↔ Estoque ↔ Previsão. Mensagens possuem `performative`, `ontology`,
`thread` e JSON validado. Não é criada uma comunicação direta entre Atendimento
e Previsão para facilitar o experimento.

Envio e recebimento são eventos distintos. Uma troca request/inform completa
pode gerar quatro registros: envio da requisição, recebimento da requisição,
envio da resposta e recebimento da resposta. O protocolo confere internamente
essa relação por `thread` e exporta contagens por ontology e performative;
não se deve interpretar quatro registros como quatro operações de negócio.
Respostas `failure` são evidência de falha
controlada, incluindo as rejeições esperadas nos cenários negativos.
As duas rejeições esperadas do protocolo são vendas recusadas por quantidade
superior ao disponível e por saldo zerado. Elas não são falhas de transporte.

O payload da auditoria permanece omitido. A evidência de transporte não precisa
copiar texto de clientes, pedidos ou previsões. Tempos de resposta, quando
coletados, usam relógio monotônico e descrevem um caso completo, incluindo suas
operações e verificações. Há uma amostra por caso; preparação, inicialização
e os quatro pings de aquecimento ficam fora dessa duração. Casos diferentes
não são repetições da mesma RPC. Esses tempos não sustentam percentis de
latência de uma operação, um SLA ou um teste de carga de vários usuários.

## Avaliação temporal das previsões

A coleta histórica consome apenas os artefatos publicados e confere sua
integridade. Não desserializa modelos pickle nem relê o CSV bruto. Os valores
exportados são os resultados da execução original, com sua versão, parâmetros,
calendário, corte e proveniência; a ferramenta não recalcula o vencedor.

O protocolo mantém as decisões de dados já aprovadas:

- O alvo é a quantidade comercial pedida no dia escrito em `pvEmis`, sem deslocamento
  de fuso ou de entrega.
- A primeira avaliação usa histórico de 2022 em diante. O ano de 2021 é
  preservado, mas não entra nesse recorte devido ao calendário excepcional.
- O calendário operacional de Manaus distingue dias abertos e fechados.
  Lacunas em dias abertos dependem da completude confirmada da exportação.
- Códigos externos são textuais e preservam zeros à esquerda. Um código sem
  associação ao catálogo continua pertencendo à publicação histórica.
- A coleta não amplia o corte ou o período aprovado só porque o relógio avançou.

O motor compara `naive`, `seasonal_naive`, `random_forest` e
`hist_gradient_boosting` na validação temporal quando há histórico suficiente.
Cada fold treina apenas com datas anteriores à sua validação. A escolha usa
a menor MAE nos dias abertos da validação; em empate, prefere o modelo mais
simples conforme a ordem definida no motor.

O teste final fica separado e mede o vencedor e os dois baselines. Seu
resultado não seleciona novamente o modelo. Os modelos candidatos que não
foram escolhidos não recebem métricas de teste inventadas. Uma série curta
pode usar fallback sem avaliação; ausência de métrica permanece ausente.

As métricas de validação agrupam os dias abertos avaliados nos folds, conforme
o resultado original do motor; não são a média dos valores de cada fold.
A comparação registra melhor, empate, pior ou não comparável para cada métrica
e baseline. A diferença absoluta de WAPE/sMAPE usa pontos percentuais; o ganho
relativo usa o erro do baseline como denominador e permanece indefinido quando
esse erro é zero. A condição de superar estritamente ambos os baselines usa
MAE do teste, sem transformar resultados de outras métricas em vitória geral.

O relatório deve distinguir três intervalos:

| Intervalo | Interpretação |
|---|---|
| Treino e validação de cada fold | Evidência da escolha temporal entre candidatos. |
| Treino anterior ao teste e janela de teste final | Evidência usada para medir o vencedor e os baselines. |
| Histórico até o corte da publicação | Ajuste final para produzir os dias futuros, após avaliação. |

O ajuste final pode incluir observações que antes compuseram a janela de teste.
Isso ocorre após a avaliação e não significa que essas observações treinaram
o modelo que produziu as métricas de teste registradas.

MAE e RMSE usam as unidades de quantidade do produto. WAPE e sMAPE são
percentuais. Valores menores indicam menor erro naquela janela. WAPE pode ser
indefinida quando a demanda observada é zero e há erro; não convertê-la para
zero. Toda comparação exige o mesmo produto, período e conjunto de dias
elegíveis. Superar um baseline não significa superar os dois.

A visão geral é verificada por coerência: soma dos produtos e agregações
diária, semanal e mensal devem concordar usando quantidades decimais exatas.
Não se calcula uma média dos MAEs ou WAPEs para apresentá-la como erro geral.
O total comercial reúne unidades possivelmente heterogêneas e não representa
litros, peso, faturamento ou necessidade de matéria-prima.

## Reprodutibilidade e limites

Para reproduzir a coleta, conserve localmente os manifestos e os arquivos
identificados por seus hashes, o ambiente com as versões fixadas, os argumentos
da execução e o protocolo utilizado. Comparações entre publicações precisam
registrar cortes, janelas, parâmetros e cobertura, evitando tratar testes
sobrepostos como réplicas independentes.

O JSON registra versão do protocolo, Python, plataforma, dependências, `HEAD`
Git e a indicação de alterações na árvore de trabalho. Também registra hashes
dos fontes Python da aplicação e dos comandos, além dos arquivos de requisitos.
Um `HEAD` com `worktree_dirty=true` não descreve sozinho todo o código executado; os hashes
do snapshot complementam essa referência. Os artefatos de previsão conservam
sua própria proveniência e versões originais, distintas do ambiente da coleta.

Uma publicação oferece folds de validação e uma origem final de teste. Esses
resultados permitem comparar quantitativamente erros observados nesse recorte.
Não demonstram significância estatística, superioridade universal de um modelo
ou eficácia da previsão na operação real. A validação funcional usa dados
fictícios; a avaliação histórica usa uma fonte operacional específica e depende
das decisões de calendário e completude aprovadas.

O experimento não substitui a suíte automatizada, a revisão das fontes ou uma
avaliação posterior com novos períodos. Nenhuma alegação de melhoria deve ser
feita quando a comparação quantitativa não a sustenta.

## Proteção dos dados

Todos os resultados, inclusive tabelas, resumos, gráficos, capturas e cópias,
são restritos e permanecem em `data/`. Eles não devem ser anexados ao GitHub,
enviados a serviços externos ou copiados para este guia. O mesmo cuidado vale
após desidentificação e para os dados fictícios gerados pelo comando, que ficam
nas mesmas pastas privadas por uniformidade do protocolo.

Os caminhos são validados como privados, ignorados e fora do índice Git.
Links simbólicos e entradas fora das pastas permitidas são rejeitados. Saídas
novas não sobrescrevem arquivos anteriores e usam permissões privadas.
A documentação pública descreve metodologia e comandos; não publica códigos,
categorias, quantidades, métricas ou hashes reais.

Antes de concluir alterações:

```bash
python3 scripts/check_private_data.py
git diff --check
```

A proteção verifica caminhos no índice Git. Ela não faz auditoria do histórico
inteiro de commits nem detecta registros reais copiados para um arquivo de
código ou documentação. Nenhum teste deve depender do CSV ou banco real.

## Verificação da implementação

O fechamento da fase exige executar a coleta local, revisar suas evidências
privadas e passar a suíte completa e a seleção SPADE do CI em sequência.
Além dos testes do novo protocolo, permanecem as regressões de atendimento,
estoque, previsões, alertas e interface.

```bash
python -m pip check
node --check app/static/js/estoque.js
node --check app/static/js/previsoes.js
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

Validação final em 23/10/2026:

- **47 novos testes** do protocolo aprovados.
- **1.129 testes** aprovados na suíte completa.
- **61 testes** aprovados na seleção adicional com SPADE/XMPP real; essa seleção
  se sobrepõe à suíte completa e foi executada depois dela.
- Dependências, JavaScript, proteção do índice Git e `git diff --check` aprovados.

A coleta local utilizou a publicação que já estava ativa, escolhida antes de
examinar as métricas. As **31 verificações** funcionais passaram, com **22
tempos** de casos completos registrados. A avaliação temporal e as comparações
com baselines foram exportadas para os arquivos privados, com evidência
quantitativa disponível no teste final e os limites de interpretação descritos
neste guia.

A conferência de hashes confirmou que o banco operacional, os artefatos
publicados e o código executado permaneceram inalterados durante a validação.
As evidências de preservação e os relatórios JUnit do CI também permaneceram
em `data/reports/`, sem reproduzir conteúdo histórico na documentação pública.
Os avisos de teste restantes são depreciações existentes das dependências.

Estado: **Fase 10 implementada e executada no escopo inicial aprovado**.
A Fase 11 é opcional e não foi iniciada.
