# Fase 6 — Motor de previsão de demanda

## Escopo e decisões de dados

O motor local gera previsões diárias por código externo, compara quatro modelos
em ordem temporal e deriva as visões semanal, mensal e geral da mesma previsão
diária. A execução usa somente o dataset diário preparado e o recorte aprovado
na [revisão da Fase 5](FASE_5_IMPORTACAO.md). O CSV bruto e o SQLite permanecem
inalterados; não há nova migração de schema nesta fase.

O usuário autorizou a avaliação inicial com histórico a partir de **2022**.
Os registros de 2021 permanecem no histórico canônico, mas não entram nesse
experimento, pois a operação durante a pandemia foi diferente e não existe um
calendário operacional completo confirmado daquele período.

A data continua sendo o dia escrito em `pvEmis`, sem conversão para Manaus e sem
deslocamento para a entrega. Na Quarta-feira de Cinzas a fábrica recebe pedidos
para o dia seguinte: esses pedidos pertencem ao dia de emissão. Valores extremos
e códigos sem vínculo com o catálogo não são removidos ou associados por nome.
O relatório preserva grupo e categoria históricos por código externo; esses
rótulos não substituem a categoria utilizada pelo Atendimento.

## Organização

| Módulo | Responsabilidade |
|---|---|
| `app/forecasting/calendar.py` | Calendário de Manaus, fontes legais e exceções operacionais |
| `app/forecasting/dataset.py` | Contrato diário, recorte, lacunas e perfil das séries |
| `app/forecasting/features.py` | Defasagens e médias que usam somente o passado |
| `app/forecasting/baselines.py` | Referências naive e sazonal de sete dias |
| `app/forecasting/models.py` | Random Forest e HistGradientBoosting, ajuste e previsão recursiva |
| `app/forecasting/evaluation.py` | Validação temporal, teste final e métricas |
| `app/forecasting/aggregation.py` | Agregações decimais coerentes e cobertura dos períodos |
| `app/forecasting/service.py` | Proveniência, execução privada, artefatos e consulta |
| `scripts/forecast_history.py` | Comandos locais `train` e `query` |

O serviço não inicializa Flask, banco ou runtime SPADE. O Agente de Previsão,
as respostas XMPP efetivas, a persistência em `PrevisaoDemanda` e a integração
com Estoque pertencem à Fase 7. O contrato de consulta XMPP existente permanece
com `NOT_IMPLEMENTED`. A Fase 6 não adiciona uma tela de previsões.

## Entradas, calendário e lacunas

A execução exige `data/reports/revisao-decisoes-<id>.json` com status
`revisado_com_recorte`, política `dia_fonte`, exportação completa confirmada e
o schema autorizado da preparação: seis colunas na versão 2 ou sete na versão 3,
com `nome_completo`. Confere a preparação aprovada e o pareamento entre versão,
cabeçalho e colunas declaradas nos relatórios, os
caminhos privados e os hashes dos datasets canônico e revisado. Não relê o CSV
original. Alterações de dados após a revisão bloqueiam a execução.

O nome comercial completo é metadado de identificação, preservado no relatório
quando presente no dataset. Ele não altera os alvos, as quantidades ou os
cálculos de previsão e não é usado para inferir associações automaticamente.

O calendário legal é separado do calendário efetivo da fábrica. A versão
`manaus-2021-2027-v1` cobre somente os anos explicitamente solicitados entre
2021 e 2027. Consultar outro ano gera erro, inclusive em uma previsão que cruze
para 2028. A projeção para 2027 usa leis recorrentes vigentes verificadas em
22/10/2026; pontos facultativos de 2026 não são projetados para outros anos.

As regras incluem:

- Segunda a sexta são dias possíveis de operação; sábados e domingos fecham.
  Feriados em fins de semana não são transferidos automaticamente para segunda.
- Os feriados nacionais fixos seguem as [Leis 662/1949 e 10.607/2002](https://www.planalto.gov.br/ccivil_03/leis/l0662.htm)
  e a [Lei 6.802/1980](https://www.planalto.gov.br/ccivil_03/leis/l6802.htm).
- O dia 20 de novembro já era feriado estadual pela [Lei AM 84/2010](https://sapl.al.am.leg.br/media/sapl/public/normajuridica/2010/2873/2873_texto_integral.pdf).
  A [Lei federal 14.759/2023](https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/l14759.htm)
  foi publicada em 22/12/2023; a classificação nacional se aplica aos dias
  20 de novembro a partir de 2024, preservando a regra estadual anterior.
- Cinco de setembro segue a [Lei AM 25/1977](https://sapl.al.am.leg.br/norma/8919).
  A [Lei Orgânica de Manaus, art. 437](https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/2005/6591/loman_atualizada_ate_emenda_120_19a_leg.pdf)
  também prevê 5 de setembro e 24 de outubro. Oito de dezembro decorre da
  [Lei municipal 496/1999](https://sapl.cmm.am.gov.br/norma/3932).
- Terça-feira de Carnaval fecha por lei municipal. Quarta-feira de Cinzas é
  feriado somente até 12h; na frequência diária, `is_open` permanece verdadeiro,
  pois existe expediente possível à tarde. A [Lei municipal 448/1998](https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/1998/3871/lei_n_448_de_11_nov_1998.pdf)
  não transforma a tarde em feriado. A informação operacional do usuário também
  confirma que pedidos são recebidos nesse dia.
- Paixão de Cristo e Corpus Christi seguem as leis municipais 1.001/2006 e
  970/2006, referidas no [calendário oficial municipal de 2026](https://www.manaus.am.gov.br/wp-content/uploads/2026/02/Prefeitura-de-Manaus-publica-calendario-de-feriados-e-pontos-facultativos-municipais-no-Diario-Oficial.pdf).
  Datas móveis usam o computus gregoriano, sem consulta à internet na execução.
- Carnaval e Cinzas de 2021 são exceções legais específicas: a suspensão do
  Carnaval aparece na [deliberação oficial do CREA-AM 19/2021](https://crea-am.org.br/creaam_site/wp-content/uploads/DD-19-2021-APROVACAO-DO-CALENDARIO-DE-FERIADOS-MUNICIPAIS-E-NACIONAIS-DE-2021.pdf),
  e o [Decreto municipal 5.025/2021](https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/2021/2142/lei_n_2731_de_15_fev_2021.pdf)
  suspendeu Cinzas. A [Lei 2.735/2021](https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/2021/2254/lei_n_2735_de_30_mar_2021.pdf)
  revogou a suspensão geral anterior e autorizou novos atos; o calendário não
  interpreta essa autorização como cancelamento automático de todo feriado.
- Pontos facultativos e emendas do serviço público não fecham automaticamente
  a fábrica privada. O [decreto municipal de 2026](https://www.manaus.am.gov.br/wp-content/uploads/2026/02/Prefeitura-de-Manaus-publica-calendario-de-feriados-e-pontos-facultativos-municipais-no-Diario-Oficial.pdf)
  distingue esses dias dos feriados e trata do expediente administrativo.

Cada série começa na primeira observação elegível do produto e termina no
corte explícito. O motor não cria demanda nem zeros antes desse primeiro
registro. Dias fechados recebem zero operacional. Dias abertos sem registro
recebem zero somente porque o usuário confirmou que a exportação contém todos
os pedidos do período. Sem essa confirmação, uma lacuna aberta é bloqueante.
O fim das vendas de um código não comprova descontinuação do produto; zeros
posteriores ao último registro devem ser interpretados com essa limitação.

Observações em dias fechados ou posteriores ao corte são excluídas apenas da
série do experimento. O relatório contabiliza motivos e quantidades, sem alterar
a fonte. Uma observação futura tem prioridade de classificação sobre fechamento,
evitando dupla contagem. Duplicatas diárias e metadados comerciais divergentes
geram erro, em vez de somas ou correções silenciosas.

## Execução local

Instale as dependências fixadas do projeto no ambiente virtual:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m scripts.forecast_history --help
```

Substitua `<id>` pelo identificador da revisão privada aprovada. Informe como
corte o último dia **completo** conhecido, nunca um dia parcialmente exportado.
O corte não pode superar a referência da revisão. Para uma revisão referenciada
em 22/10/2026, a configuração inicial utiliza a véspera:

```bash
python -m scripts.forecast_history train \
  --review 'data/reports/revisao-decisoes-<id>.json' \
  --experimental-selection 'data/reports/selecao-experimental-<id>.json' \
  --start 2022-01-01 \
  --cutoff 2026-10-21 \
  --horizon 28 \
  --validation-days 14 \
  --folds 3 \
  --test-days 14
```

`--horizon`, `--validation-days` e `--test-days` contam dias de calendário,
incluindo os dias fechados, e aceitam de 1 a 366. `--folds` aceita de 1 a 10.
Após a adaptação experimental, exporte antes a seleção auditada com
`flask --app app.app:create_app previsao exportar-selecao`. O argumento
`--experimental-selection` é obrigatório para treinar pela CLI; seleção ausente
não autoriza usar todo o histórico. `--code` é opcional e repetível, mas, quando
informado, deve corresponder exatamente ao conjunto do snapshot. Omitir `--start`
usaria o primeiro registro elegível disponível, portanto o comando inicial
explicita a exclusão experimental de 2021.

A CLI imprime somente os caminhos privados criados e o status. Códigos reais,
categorias, quantidades, métricas e hashes permanecem nos arquivos restritos.
O código de saída é `0` para conclusão e `2` para falha controlada. Não existe
upload, publicação no GitHub nem envio a serviço externo.

## Exceções operacionais privadas

Fechamentos internos, recessos ou aberturas excepcionais confirmados podem ser
registrados em um JSON privado dentro de `data/artifacts/`. Exemplo inteiramente
fictício de formato:

```json
{
  "overrides": [
    {"date": "2026-07-06", "is_open": false},
    {"date": "2026-07-11", "is_open": true}
  ]
}
```

Acrescente `--calendar-overrides data/artifacts/calendario-operacional.json`
ao treinamento. `true` abre o dia; `false` fecha. Datas repetidas, não cobertas
ou valores que não sejam booleanos são rejeitados. A exceção prevalece sobre a
regra semanal e legal, mas não altera o nome nem a origem legal do feriado.
As exceções utilizadas ficam nos metadados do calendário da execução.

**Reabrir um dia não recupera observações removidas do recorte aprovado.**
Se a revisão anterior desconsiderou sábado, domingo ou outra data e a decisão
operacional mudou, é necessário produzir e aprovar uma nova revisão do dataset.
Um override isolado sobre dados já excluídos poderia criar um zero indevido;
o calendário não relê nem reconstrói esses pedidos a partir do CSV bruto.

## Modelos e avaliação temporal

Os dois baselines são referências fixadas no instante de origem:

- `naive`: repete o último valor diário observado no histórico de treino;
- `seasonal_naive`: repete o último ciclo de sete dias, com fallback para o
  último valor quando ainda não existem sete dias.

O calendário mascara as previsões de dias fechados para zero, sem modificar o
valor ou ciclo histórico que define o baseline. Assim, um fechamento no horizonte
não muda artificialmente o padrão dos dias seguintes. O naive literal pode
repetir zero quando o último dia histórico estava fechado; o relatório compara
explicitamente essa referência com o baseline sazonal.

Random Forest e HistGradientBoosting usam `lag_1`, `lag_7`, `lag_14`, `lag_28`,
médias dos 7, 14 e 28 dias anteriores, dia da semana, semana ISO, mês e indicador
de fim de semana. Lags são em dias de calendário. As médias usam `shift(1)`
antes do rolling, sem incluir o próprio alvo. A validação em ordem temporal e
os cuidados com defasagens seguem a orientação da [documentação oficial do
scikit-learn sobre forecasting com lags](https://scikit-learn.org/1.7/auto_examples/applications/plot_time_series_lagged_features.html).

A previsão dos candidatos é recursiva: cada dia utiliza somente o treino e as
previsões anteriores da mesma janela. Não consome os alvos reais da validação ou
do teste. Valores negativos previstos são truncados para zero e valores não
finitos interrompem a execução. Os estimadores usam `random_state=42`; Random
Forest usa um trabalhador e as bibliotecas numéricas têm limite de uma thread.
O HistGradientBoosting usa `early_stopping=False`, sem holdout interno aleatório.

O procedimento por produto é:

1. Separar os últimos 14 dias para teste final, sem consultá-los na seleção.
2. Comparar os modelos em três janelas consecutivas de 14 dias, com origem
   avançando e treino crescente. Cada treino termina antes de sua validação.
3. Selecionar o menor **MAE nos dias abertos** concatenados da validação.
   Empates exatos favorecem naive, sazonal, Random Forest e HistGradientBoosting,
   nessa ordem fixa.
4. Reajustar o vencedor antes do teste e medir esse vencedor e os dois baselines
   no teste final. Essas métricas não trocam o vencedor da validação.
5. Ajustar novamente o modelo selecionado em todo o histórico elegível até o
   corte e prever os próximos 28 dias.

Essas durações são os parâmetros iniciais, não valores fixos na API. Os
candidatos supervisionados exigem pelo menos 60 linhas utilizáveis após os
28 dias iniciais de lags em todos os folds; caso contrário ficam indisponíveis
com motivo registrado. Se não houver histórico suficiente para separar as
janelas, ou faltarem dias abertos nas janelas de avaliação, o motor usa fallback
sazonal/naive. Nesse caso, registra `fallback=true`, motivo e métricas ausentes;
não apresenta métricas de treino como evidência fora da amostra.

MAE e RMSE usam a unidade comercial de quantidade do produto. WAPE e sMAPE são
percentuais, com tratamento de denominadores zero. WAPE é `null` se não houver
demanda real e existir erro; quando observação e previsão são ambas zero, retorna
zero. MAPE não é utilizado. O relatório informa se o vencedor supera estritamente
ambos os baselines no teste; essa superioridade não é presumida pelo tipo de
modelo nem garantida pelo treinamento.

## Agregações e consulta

Cada previsão diária de produto é quantizada uma única vez para nove casas
decimais com `ROUND_HALF_EVEN`. As somas seguintes usam `Decimal`, e quantidades
saem como texto decimal canônico. O geral é a soma diária dos produtos; semanas
vão de segunda a domingo; meses seguem o calendário civil. Não há modelos
independentes para geral, semana ou mês.

Cada período inclui início/fim civil, início/fim efetivamente coberto e o
indicador `parcial`. Um horizonte que comece no meio da semana ou termine antes
do fim do mês não é extrapolado para preencher o período. Os totais conservam
exatamente a soma dos mesmos dias e produtos. O geral soma quantidades comerciais
heterogêneas: não representa litros, massa ou faturamento.

Consulte o manifesto de uma execução concluída. Sem `--code`, a consulta retorna
o geral; o código abaixo é apenas um identificador fictício de exemplo:

```bash
python -m scripts.forecast_history query \
  --manifest 'data/artifacts/previsao-<id>/manifest.json' \
  --granularity semanal

python -m scripts.forecast_history query \
  --manifest 'data/artifacts/previsao-<id>/manifest.json' \
  --code CODIGO_FICTICIO \
  --granularity mensal
```

A consulta salva `data/reports/consulta-previsao-<id>.json`, sem imprimir seu
conteúdo. Confere status/versão do manifesto, vínculo com a execução e hash do
JSON de previsões. Não desserializa arquivos pickle para consultar resultados.

## Artefatos e rastreabilidade

| Caminho privado | Conteúdo |
|---|---|
| `data/processed/previsao-<id>/series_diarias.csv` | Série diária utilizada e indicador de dia aberto |
| `data/artifacts/previsao-<id>/previsoes.json` | Previsões, modelos escolhidos, cobertura e agregações |
| `data/artifacts/previsao-<id>/modelo-<sequencia>.pkl` | Forecaster ajustado por produto, com histórico e calendário |
| `data/reports/previsao-<id>.json` | Proveniência, calendário, recortes, métricas, parâmetros e limitações |
| `data/reports/previsao-<id>.md` | Resumo privado da comparação por produto |
| `data/artifacts/previsao-<id>/manifest.json` | Conclusão, caminhos, hashes dos artefatos/código e versões do ambiente |

O manifesto registra Python, NumPy, pandas, scikit-learn e threadpoolctl, além de
SHA-256 dos módulos do motor e da CLI. É gravado por último com
`status=concluido`; uma execução interrompida sem esse manifesto não é um
resultado concluído consultável. Arquivos parciais eventualmente existentes
continuam privados e não significam sucesso. A serialização de modelos também
contém histórico e deve receber a mesma proteção do dataset.

Entradas e saídas devem estar ignoradas e fora do índice Git. Caminhos fora dos
diretórios previstos, links simbólicos e arquivos com vínculos físicos adicionais
são rejeitados. Arquivos novos têm permissão 0600 e diretórios novos 0700.
Relatórios, modelos, séries, consultas, gráficos e qualquer exportação derivada
de dados reais permanecem em `data/`, inclusive após desidentificação.

## Validação

```bash
python -m pytest -q tests/test_forecasting_calendar.py \
  tests/test_forecasting_dataset.py tests/test_forecasting_models.py \
  tests/test_forecasting_aggregation.py tests/test_forecasting_service.py
python -m pytest -q
python -m pip check
node --check app/static/js/estoque.js
python3 scripts/check_private_data.py
git diff --check
```

Os testes utilizam somente séries, códigos, repositórios e arquivos fictícios.
Cobrem calendário e vigência legal, bordas de ano, exceções operacionais,
contrato/duplicatas/metadados, lacunas, ausência de vazamento nas features e na
previsão recursiva, separação treino/validação/teste, baselines, fallback,
métricas com zeros, agregações exatas, períodos parciais, cadeia de hashes,
permissões, falhas de gravação/modelo, consulta e CLI sem exposição de conteúdo.

A validação automatizada da implementação registrou **576 testes aprovados**,
incluindo **201 novos testes** desta fase. Foram emitidos 129 avisos: 128 de
`datetime.utcnow()` legado e um de `pytz`. Esse resultado verifica o código em
cenários fictícios; resultados e métricas do histórico real pertencem aos
relatórios privados de cada execução concluída.

A primeira execução local com o histórico revisado foi concluída usando dados
de 2022 em diante, corte no último dia completo (21/10/2026) e horizonte de
28 dias. A verificação dos artefatos confirmou os hashes, as janelas temporais,
o calendário e a igualdade das agregações. Modelos salvos e recarregados
reproduziram as previsões. O banco, o histórico preparado e o recorte revisado
permaneceram inalterados; o CSV original não foi lido pelo motor. Os resultados
por produto e a comparação no teste final estão somente no relatório privado.
