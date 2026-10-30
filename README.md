# TCC - Sistema de Atendimento para Sorveteria

Este repositório contém o protótipo desenvolvido para o Trabalho de Conclusão de Curso em Sistemas de Informação da UEA - EST.

O projeto simula um sistema de atendimento virtual para uma sorveteria, com foco em:

- atendimento conversacional;
- consulta de produtos, preços e disponibilidade;
- registro de vendas;
- persistência de histórico por cliente;
- confirmação de pedidos;
- suporte a pedidos com múltiplos produtos;
- produtos com mesma variação/sabor em categorias/tamanhos diferentes.

O protótipo atual utiliza Flask, SQLite e SQLAlchemy. Na Fase 2, o atendimento foi
separado em um serviço de aplicação e módulos de diálogo determinísticos.
Na Fase 3, `AgentGateway` conecta o Flask ao runtime com os três agentes
SPADE e servidor XMPP local. `AtendimentoAgent` mantém a fachada compatível
para execução local sem runtime.
Na Fase 4, `EstoqueAgent` passa a consultar disponibilidade e registrar vendas,
baixas, movimentações e alertas em transações coordenadas. A tela `/estoque`
inclui o controle manual de produtos acabados e matérias-primas.
Na Fase 5, um comando local importa o histórico diário já preparado, preserva
grupo/categoria e permite reconstruir séries por código externo. Consulte o
[guia de importação](docs/FASE_5_IMPORTACAO.md) para validação, repetição segura
e relatório privado.
Na Fase 6, o motor local avalia baselines e modelos por produto em ordem
temporal, gera previsões coerentes diária/semanal/mensal e salva resultados e
artefatos privados. Consulte o [guia do motor](docs/FASE_6_PREVISAO.md).
Na Fase 7, `PrevisaoAgent` responde ao Estoque por XMPP, publica previsões e
métricas no SQLite e executa recálculos sob comando em segundo plano. A visão
geral pode ser consultada sem vincular produtos ao catálogo; consultas por item
exigem `Produto.codigo_externo` explicitamente associado. Consulte o
[guia do agente de previsão](docs/FASE_7_AGENTE_PREVISAO.md) para configuração,
migração e comandos locais.
Na Fase 8, o Estoque registra alertas preventivos quando a demanda prevista
deixa o saldo projetado abaixo do mínimo. A atualização automática reúne
solicitações após mudanças de estoque ou previsão; a tela também permite
consultar o risco e solicitar uma avaliação. Previsão indisponível preserva os
alertas anteriores. Consulte o [guia dos alertas](docs/FASE_8_ALERTAS_PREVENTIVOS.md).
Na Fase 9, a área `/previsoes` reúne filtros por código histórico, período e
frequência, gráfico local, tabela e avaliação temporal por produto. As quantidades
da interface são estimativas inteiras; os decimais originais ficam no backend.
O recálculo usa o agente existente em segundo plano, com acompanhamento da
tarefa. Consulte o [guia da interface](docs/FASE_9_INTERFACE_WEB.md).
Na Fase 10, um comando local organiza cenários fictícios com SPADE/XMPP real e
coleta a avaliação temporal de manifestos publicados, sem novo treinamento com dados reais nem
alteração do banco operacional. Evidências CSV/JSON e resumo permanecem privados.
Consulte o [protocolo experimental](docs/FASE_10_VALIDACAO_EXPERIMENTAL.md).

Após a validação manual, o Atendimento informa disponibilidade sem divulgar
saldos. A tela de Estoque apresenta mensagens por formulário, mínimo com passo
de uma unidade e entrada decimal, quantidade inicial opcional de matéria-prima
e um resumo de alertas na navegação. As previsões mostram quantidades inteiras
somente na apresentação, com datas adaptativas no gráfico. Consulte as
[correções da validação manual](docs/CORRECOES_VALIDACAO_MANUAL.md) para as regras
de arredondamento, transações, identificação histórica e validação.

## Arquitetura


```text
Interface Flask
   ↓
Rotas HTTP / Templates / APIs
   ↓
AgentGateway → Runtime SPADE (loop dedicado)
   ↓
AtendimentoSPADEAgent ↔ EstoqueAgent ↔ PrevisaoAgent
   ↓
AtendimentoService → IntentParser / OrderParser / ResponseBuilder
   ↓
EstoqueClient → EstoqueAgent / EstoqueService
   ↓
Modelos SQLAlchemy
   ↓
SQLite
```

### Fluxo do atendimento

```text
Cliente envia mensagem
   ↓
Rota Flask recebe o texto
   ↓
AtendimentoService interpreta a intenção com IntentParser
   ↓
Serviço identifica itens com OrderParser e consulta disponibilidade no Estoque
   ↓
Se for pedido, salva contexto e pede confirmação
   ↓
Cliente confirma
   ↓
Estoque registra venda, baixa, movimentação, alerta e chave de operação juntos
   ↓
Histórico da conversa é salvo por cliente
```

## Tecnologias utilizadas

### Principais no protótipo atual

- Python 3.12: ambiente usado na validação do baseline (3.12.3).
- Flask: framework web usado para rotas, telas e APIs.
- Flask-SQLAlchemy: integração entre Flask e SQLAlchemy.
- SQLite: banco de dados local do protótipo.
- HTML/Jinja2: templates renderizados pelo Flask.
- CSS: estilização da interface.
- SPADE 4.1.4 / PyJabber 0.4.5: agentes e comunicação XMPP local.

### Dados e previsão


- Pandas e NumPy: séries, features e cálculos do motor de previsão.
- Scikit-learn: candidatos Random Forest e HistGradientBoosting da Fase 6.
- threadpoolctl: limita paralelismo nativo durante ajuste e inferência.
- Streamlit: dependência herdada, sem uso no código atual; a interface continua em Flask.

As dependências diretas têm versões fixadas em `requirements.txt`, incluindo SQLAlchemy,
usado diretamente pelo projeto. `requirements-dev.txt` acrescenta pytest. A Fase 0
não implementa previsão, novos agentes, integração de LLM ou receitas/BOM.

## Estrutura do projeto

```text
TCC/
├── app/
│   ├── agents/
│   │   ├── atendimento_agent.py
│   │   ├── base_agent.py
│   │   ├── config.py
│   │   ├── estoque_agent.py
│   │   ├── previsao_agent.py
│   │   ├── protocol.py
│   │   ├── gateway.py
│   │   ├── runtime.py
│   │   └── xmpp_server.py
│   ├── dialogue/
│   │   ├── intent_parser.py
│   │   ├── order_parser.py
│   │   └── response_builder.py
│   ├── models/
│   │   ├── cliente.py
│   │   ├── contexto_conversa.py
│   │   ├── historico_conversa.py
│   │   ├── produto.py
│   │   └── venda.py
│   ├── forecasting/
│   │   ├── calendar.py
│   │   ├── dataset.py
│   │   ├── features.py
│   │   ├── baselines.py
│   │   ├── models.py
│   │   ├── evaluation.py
│   │   ├── aggregation.py
│   │   └── service.py
│   ├── experiments/          # cenários e coleta local de evidências
│   ├── routes/
│   │   ├── estoque_routes.py
│   │   ├── previsao_routes.py
│   │   ├── produto_routes.py
│   │   └── venda_routes.py
│   ├── services/
│   │   ├── atendimento_service.py
│   │   ├── estoque_client.py
│   │   ├── estoque_service.py
│   │   ├── importacao_service.py
│   │   ├── previsao_painel_service.py
│   │   └── venda_service.py
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css
│   │   └── js/
│   │       ├── estoque.js
│   │       └── previsoes.js
│   ├── templates/
│   │   ├── base.html
│   │   ├── estoque.html
│   │   ├── previsoes.html
│   │   ├── simulacao_venda.html
│   │   └── vendas.html
│   ├── app.py
│   └── database.py
├── data/                     # privado, ignorado pelo Git
│   └── artifacts/
│       └── sorvetes.db
├── create_db.py
├── seed.py
├── run.py
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── tests/
│   ├── conftest.py
│   ├── test_regressao_tcc_i.py
│   ├── test_venda_service.py
│   ├── test_atendimento_contexto.py
│   └── test_http.py
├── docs/
│   └── BASELINE_FASE_0.md
├── scripts/
│   ├── prepare_history.py
│   ├── import_history.py
│   ├── forecast_history.py
│   └── validate_tccii.py
└── README.md
```

## Função dos principais arquivos

### `run.py`

Arquivo de entrada da aplicação. Inicializa o runtime SPADE/XMPP e o Flask,
com encerramento explícito e reloader desativado. Configure as três senhas
XMPP no ambiente conforme o [guia da Fase 3](docs/FASE_3_SPADE_XMPP.md).

```bash
.venv/bin/python run.py
```

### `app/app.py`

Define a função `create_app(test_config=None)`, responsável por:

- criar a aplicação Flask;
- configurar o banco SQLite;
- inicializar o SQLAlchemy;
- registrar os blueprints de atendimento, produtos, estoque e previsões.

Sem argumentos, usa o caminho absoluto `data/artifacts/sorvetes.db`, validado
como privado e ignorado pelo Git. Nos testes,
a configuração é substituída antes de inicializar o SQLAlchemy para usar
um arquivo SQLite temporário exclusivo de cada teste.
`TESTING=True` sem URI explícita usa memória. Diretórios e arquivos novos do
banco padrão usam permissões privadas. Se existir apenas o banco legado em
`instance/`, a fábrica exige a transferência antes de iniciar, evitando abrir
um banco vazio por engano.

### `app/database.py`

Cria o objeto global `db`, usado pelos modelos SQLAlchemy.

### `create_db.py`

Cria ou atualiza o SQLite usando as revisões Alembic em `migrations/`.
Antes de uma atualização de banco existente, salva uma cópia privada em
`data/artifacts/backups/`. A atualização é transacional e preserva vendas,
conversas, clientes e produtos. Também pode ser executada por:

```bash
python -m flask --app run.py db upgrade
python -m flask --app run.py db current
```

Use esse comando antes de iniciar a aplicação após atualizar o código. Não é
necessário executar o seed novamente. Detalhes: [Fase 1](docs/FASE_1_MODELOS_MIGRACOES.md).

### `seed.py`

Popula o banco com dados iniciais:

- 11 produtos simulados por categoria e sabor, como caixa de 10L, caixa de 5L, sundae e picolé;
- cinco clientes simulados.

O script não duplica os produtos e clientes iniciais, mas cada execução redefine
os preços e os saldos dos 11 produtos para os valores iniciais (20 unidades por
produto), reativa esses produtos e desativa os demais. Execute-o para preparar
a demonstração; ele não preserva os saldos alterados por vendas anteriores.

## Modelos do banco

### `Produto`

Arquivo: `app/models/produto.py`

Representa os produtos vendidos pela sorveteria.

Campos principais:

- `id`
- `nome`
- `categoria`
- `sabor`
- `preco`
- `descricao`
- `quantidade_disponivel`
- `estoque_minimo`
- `codigo_externo` (opcional e único, preserva zeros à esquerda)
- `ativo`

O saldo de produto acabado fica em `produtos`; matérias-primas têm cadastro
próprio. As alterações operacionais geram `MovimentacaoEstoque` auditável.
Produtos com o mesmo sabor podem existir em categorias diferentes, por exemplo `caixa de 10L - chocolate` e `caixa de 5L - chocolate`.

### `Cliente`

Arquivo: `app/models/cliente.py`

Representa clientes simulados usados na tela de atendimento.

Campos:

- `id`
- `nome`
- `telefone`

### `HistoricoConversa`

Arquivo: `app/models/historico_conversa.py`

Salva o histórico de mensagens do atendimento.

Campos:

- `id`
- `cliente_nome`
- `mensagem_usuario`
- `resposta_agente`
- `timestamp`

Esse modelo permite demonstrar persistência conversacional separada por cliente.

### `ContextoConversa`

Arquivo: `app/models/contexto_conversa.py`

Guarda o estado temporário de um pedido ainda não confirmado.

Campos principais:

- `cliente_nome`
- `etapa`
- `produto_id`
- `quantidade`
- `itens_json`
- `atualizado_em`
- `operacao_id`, chave persistente para confirmação segura após timeout

O campo `itens_json` permite armazenar pedidos com múltiplos produtos antes da confirmação.

### `Venda`

Arquivo: `app/models/venda.py`

Registra vendas efetuadas.

Campos:

- `id`
- `cliente_nome`
- `produto_id`
- `quantidade`
- `valor_total`
- `data_venda`

Cada item vendido é salvo como um registro de venda. Um pedido com dois produtos gera dois registros.

### Modelos adicionados na Fase 1

- `MateriaPrima`: cadastro, unidade, saldo decimal e estoque mínimo.
- `MovimentacaoEstoque`: item, tipo, quantidade, saldos anterior/posterior e motivo.
- `AlertaEstoque`: item, tipo de alerta, nível, mensagem e resolução.
- `VendaHistorica`: demanda diária preparada, grupo/categoria e hashes de origem,
  sem dados pessoais ou identificadores individuais de pedidos.
- `PrevisaoDemanda`: produto, período, granularidade, quantidade, modelo e métricas.
- `LogMensagemAgente`: metadados de comunicação e correlação por `thread`,
  com o conteúdo do payload omitido pelo runtime.

Nesta fase foram criadas estruturas e restrições de integridade. As rotinas de
movimentação e alertas imediatos foram implementadas na Fase 4; a Fase 5 importa
o histórico diário preparado. A Fase 6 implementa o motor local de previsão;
a Fase 7 integra esse motor ao agente e persiste execuções, métricas e períodos
de previsão com quantidades decimais exatas.

A Fase 4 acrescenta `OperacaoEstoque`, com recibo persistente de operação, e
`MovimentacaoEstoque.venda_id`, único, para vincular a baixa à venda confirmada.
A revisão `0006_integridade_operacoes` restringe o tipo do recibo a `venda` ou
`movimentacao` e exige 64 caracteres no hash. A revisão 0005 permanece intacta.

## Agente de atendimento

Arquivo: `app/agents/atendimento_agent.py`

O `AtendimentoAgent` delega os métodos públicos `responder`, `registrar_venda` e
`registrar_pedido` ao `AtendimentoService`. O serviço coordena consultas, contexto
persistido por cliente e registro de vendas; os módulos em `app/dialogue/`
interpretam mensagens e formatam respostas sem acessar o banco.

O atendimento executa as seguintes funções:

- recebe mensagens de clientes;
- normaliza o texto;
- interpreta intenções simples;
- consulta produtos, preços e disponibilidade;
- identifica pedidos;
- salva contexto quando o pedido precisa de confirmação;
- registra vendas após confirmação;
- responde a erros comuns.

Intenções tratadas:

- saudação;
- consulta de sabores;
- consulta de preços;
- consulta de disponibilidade;
- disponibilidade ao cliente sem divulgação dos saldos internos;
- registro de pedido;
- confirmação;
- cancelamento.

Exemplos de mensagens aceitas:

```text
oi
quais sabores?
preco
tem chocolate?
quero 2 caixas de 10L chocolate
quero 2 caixas de 10L chocolate e 1 caixa de 5L morango
sim
não
```

Detalhes da separação e das regras preservadas:
[Fase 2 — Atendimento](docs/FASE_2_ATENDIMENTO.md).

## Serviço de vendas

Arquivo: `app/services/venda_service.py`

Mantém a API pública de vendas e delega a operação ao `EstoqueClient`.
As regras transacionais ficam em `app/services/estoque_service.py`.

Funções principais:

- `registrar_venda`: registra uma venda de um único produto.
- `registrar_vendas_multiplas`: registra pedidos com múltiplos produtos.

Antes de salvar uma venda, o serviço:

- verifica se o produto existe;
- verifica se o produto está ativo;
- verifica se a quantidade é válida;
- verifica se há estoque suficiente;
- atualiza a quantidade disponível;
- salva os registros de venda.

Itens repetidos do mesmo produto são somados antes de validar. Venda, baixa,
movimentação, atualização de alerta e recibo da operação têm commit único.
Repetir a mesma chave UUID e o mesmo pedido retorna as vendas já registradas.
Pedidos vazios são rejeitados. Nomes de cliente são normalizados com `strip()`
antes da validação, persistência e cálculo do hash. Conflitos de conteúdo com a
mesma chave retornam HTTP 409 em vendas e movimentações; chaves diferentes no
header e no corpo da mesma requisição retornam 400, sem alterar o banco.
Veja contratos e execução no [guia da Fase 4](docs/FASE_4_ESTOQUE.md).

## Rotas e telas

### Atendimento

Rota:

```text
/
```

Arquivo:

```text
app/routes/venda_routes.py
```

Template:

```text
app/templates/simulacao_venda.html
```

Tela principal do atendimento. Possui:

- chat estilo WhatsApp;
- menu lateral com os clientes cadastrados (cinco no seed);
- histórico separado por cliente;
- envio de mensagens ao agente.

### Controle de Estoque

Rota:

```text
/estoque
```

Arquivo:

```text
app/routes/estoque_routes.py
```

Template:

```text
app/templates/estoque.html
```

Tela principal de controle que mostra:

- produtos;
- descrições;
- preços;
- quantidade disponível;
- status do produto;
- status de estoque;
- matérias-primas, mínimos configuráveis, alertas e movimentações.

As consultas GET são somente leitura. Vendas e alterações de estoque/mínimo
atualizam os alertas de estoque mínimo na própria transação; o botão de
sincronização ou POST `/api/estoque/alertas/sincronizar` revisa itens legados.
Os alertas de risco futuro são reavaliados separadamente por XMPP após mudanças
de produtos ou da previsão, quando a atualização automática está ativa.
**Atualizar alerta deste produto** ou
POST `/api/estoque/alertas/preventivos/<produto_id>` solicita essa avaliação
explicitamente; **Consultar risco** não altera registros. A atualização persistente
usa exclusivamente `PREDICTIVE_ALERT_HORIZON_DAYS`, cujo padrão é sete dias, e a
tela mostra esse horizonte sem edição. A consulta de risco aceita horizontes
personalizados sem efeitos colaterais. Indisponibilidade deixa o risco desconhecido
e conserva alertas anteriores.

### Previsões de demanda

A rota `/previsoes` apresenta a publicação do Agente de Previsão, com visão
geral ou código histórico, filtros de período, agrupamentos diário/semanal/mensal,
gráfico e tabela. Modelos e métricas são exibidos por produto com seus baselines.
Códigos sem associação ao catálogo continuam disponíveis para consulta.
Um código associado mostra o nome do catálogo; um código sem descrição informa
essa ausência e identifica a categoria separadamente. Nenhum vínculo é inferido.

Cada período exibido usa arredondamento HALF_UP: fração igual ou superior a 0,5
sobe para o inteiro seguinte. Gráfico e tabela usam os mesmos inteiros, e o total
é a soma das linhas. O total pode diferir do arredondamento direto da soma decimal,
de outro agrupamento ou da soma de produtos arredondados separadamente. Previsões
publicadas, métricas e avaliação de risco continuam usando os valores originais.

O botão de atualização solicita uma tarefa por SPADE/XMPP e acompanha seu
estado, preservando a publicação anterior durante o trabalho. O recálculo exige
`FORECAST_REVIEW` configurado para uma revisão aprovada. A consulta é somente
leitura e não altera os alertas preventivos. Contratos e configuração estão no
[guia da Fase 9](docs/FASE_9_INTERFACE_WEB.md).

### Vendas

Rota:

```text
/vendas
```

Arquivo:

```text
app/routes/venda_routes.py
```

Template:

```text
app/templates/vendas.html
```

Tela para registrar venda manualmente e listar as últimas vendas.

### Catálogo e compatibilidade

Rota:

```text
/produtos
```

Essa rota redireciona para `/estoque`. O catálogo JSON permanece em `/api/produtos`.

O endpoint JSON de estoque continua disponível em `/api/estoque`.

## Endpoints da API

### Consultar produtos

```bash
curl http://127.0.0.1:5000/api/produtos
```

### Consultar estoque

```bash
curl http://127.0.0.1:5000/api/estoque
```

### Listar vendas

```bash
curl http://127.0.0.1:5000/api/vendas
```

### Registrar venda pela API

```bash
curl -X POST http://127.0.0.1:5000/api/vendas \
  -H "Content-Type: application/json" \
  -d '{"produto_id": 1, "quantidade": 1, "cliente_nome": "Cliente Teste"}'
```

### Conversar com o agente pela API

```bash
curl -X POST http://127.0.0.1:5000/api/atendimento \
  -H "Content-Type: application/json" \
  -d '{"mensagem": "quero 2 caixas de 10L chocolate e 1 caixa de 5L morango", "cliente_nome": "Cliente Simulado"}'
```

Depois, confirme o pedido:

```bash
curl -X POST http://127.0.0.1:5000/api/atendimento \
  -H "Content-Type: application/json" \
  -d '{"mensagem": "sim", "cliente_nome": "Cliente Simulado"}'
```

## Como executar o projeto

### 1. Criar ambiente virtual

```bash
python3 -m venv .venv
```

### 2. Ativar ambiente virtual

```bash
source .venv/bin/activate
```

### 3. Instalar dependências

```bash
python -m pip install -r requirements.txt
```

### 4. Criar ou atualizar banco

```bash
python create_db.py
```

### 5. Inserir dados iniciais

```bash
python seed.py
```

Em um banco já utilizado, esse comando redefine os saldos dos produtos iniciais.
Não é necessário repeti-lo a cada inicialização.

### 6. Configurar senhas e rodar aplicação

```bash
export XMPP_ATENDIMENTO_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export XMPP_ESTOQUE_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export XMPP_PREVISAO_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python run.py
```

Senhas obrigatórias vêm do ambiente; não há valor padrão. `.env.example` lista
portas e timeouts opcionais. O launcher não carrega o arquivo automaticamente.
Para usar apenas o atendimento local: `TCC_SPADE_ENABLED=0 python run.py`.
Consulte [a Fase 3](docs/FASE_3_SPADE_XMPP.md) para lifecycle e configuração.


A aplicação ficará disponível em:

```text
http://127.0.0.1:5000
```

## Testes automatizados

Com o ambiente virtual ativado:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Não é necessário executar `create_db.py` ou `seed.py` para testar. Cada teste
cria seu próprio banco em uma pasta temporária do pytest, sem usar
`data/artifacts/sorvetes.db`. As fixtures preparam os 11 produtos e cinco clientes
do baseline, sem importar os scripts de inicialização.

Os oito casos do TCC I estão em `tests/test_regressao_tcc_i.py`: catálogo,
disponibilidade específica, pedido múltiplo confirmado, categoria compartilhada,
produto sem estoque, quantidade acima do saldo, preço específico e preços múltiplos.
Os demais testes cobrem o serviço de vendas, contexto/histórico por cliente e
as telas/APIs existentes. A suíte também cobre a preparação privada dos dados,
os modelos da Fase 1 e migrações em banco vazio e legado, incluindo rollback.
A infraestrutura SPADE/XMPP tem testes reais com sockets de loopback. Para
executar os cenários de atendimento pelo gateway SPADE:

```bash
python -m pytest tests/test_regressao_tcc_i.py tests/test_atendimento_contexto.py --spade -q
```

Para executar somente os oito casos ou exportar evidências:

```bash
python -m pytest tests/test_regressao_tcc_i.py -v
python -m pytest --junitxml=/tmp/tcc-fase-0.xml
```

Consulte [o relatório da Fase 0](docs/BASELINE_FASE_0.md) para os resultados,
o escopo validado e os comportamentos conhecidos. A
[Fase 2](docs/FASE_2_ATENDIMENTO.md) registra a refatoração e sua validação atual.

## Validação experimental

A Fase 10 executa cenários controlados em banco fictício próprio e coleta,
somente para leitura, métricas e proveniência de uma previsão já publicada:

```bash
python -m scripts.validate_tccii \
  --manifest 'data/artifacts/previsao-<id>/manifest.json'
```

Substitua `<id>` pelo manifesto privado desejado. A opção pode ser repetida;
nenhum modelo é treinado ou ativado com histórico real por esse comando. A fonte
fictícia do cenário funcional gera sua própria previsão de referência. Para
executar somente os cenários fictícios, sem depender de dados reais:

```bash
python -m scripts.validate_tccii --functional-only
```

As evidências ficam em `data/reports/validacao-tccii-<uuid>/` e o workspace
fictício em `data/artifacts/validacao-tccii-<uuid>/`, fora do Git. A coleta não
altera `data/artifacts/sorvetes.db`. O console mostra somente status e caminhos.
O pacote inclui JSON, resumo e tabelas de cenários, métricas, comparações,
mensagens e tempos. Estado técnico aprovado não implica superioridade do
modelo; o modo somente funcional não fecha a avaliação integral da Fase 10.
Metodologia, comandos e limites estão no
[guia da Fase 10](docs/FASE_10_VALIDACAO_EXPERIMENTAL.md).

## Proteção dos dados históricos

O original `data/pedido.csv` é restrito e permanece apenas na máquina local.
Todos os derivados, mesmo desidentificados, devem ficar em `data/processed/`,
`data/reports/` ou `data/artifacts/`. O Git ignora toda a pasta `data/`, sem
exceção por formato, além dos formatos comuns de dados fora dela. Chaves HMAC
ficam fora do repositório. Documentação e testes usam somente dados fictícios.

Ative a proteção de commits após clonar o projeto:

```bash
chmod +x .githooks/pre-commit
git config --local core.hooksPath .githooks
python3 scripts/check_private_data.py
```

O hook verifica o índice Git e bloqueia caminhos restritos, inclusive se alguém
forçar a inclusão com `git add -f`. Ele não lê nem imprime os registros. A
configuração do hook é local e precisa ser ativada em cada clone; não desative
essa proteção. A verificação é por caminhos e formatos, não uma detecção de
informações pessoais copiadas para arquivos de código ou documentação.

A fase de preparação e desidentificação está implementada antes da Fase 1.
O CSV de entrada é `data/pedido.csv`; a descrição local dos campos está em
`data/descricao_campos.txt`. Para preparar a demanda diária, preservando o dia
de emissão conforme acordado:

```bash
python -m scripts.prepare_history --input data/pedido.csv --date-policy dia_fonte
```

O comando gera um dataset de demanda com `codigo_grupo` e `categoria`, além
de data, código do produto, quantidade e número de pedidos, e um relatório local
de qualidade em pastas ignoradas, sem modificar o original. Não grava clientes, vendedores,
nomes pessoais ou identificadores de pedidos na saída. Dados inválidos bloqueiam
a liberação do dataset e exigem revisão pelo relatório local.
Veja o [guia de preparação dos dados](docs/PREPARACAO_DADOS.md).

## Estado atual do protótipo

Fases 0, preparação dos dados, 1 (modelos/migrações), 2 (refatoração do
atendimento), 3 (infraestrutura SPADE/XMPP) e 4 (Controle de Estoque) concluídas.
A Fase 5 tem o importador implementado e o banco definitivo configurado em
`data/artifacts/sorvetes.db`. O histórico atual foi preparado, importado e
reimportado sem duplicação. A revisão privada registrou a decisão de desconsiderar
os registros apontados como fora do expediente apenas no recorte de análise,
preservando o histórico canônico. A Fase 6 implementa o motor de previsão,
com calendário verificado, avaliação temporal e artefatos locais privados.
A Fase 7 implementa o Agente de Previsão, consultas e recálculo por XMPP e
persistência transacional das execuções. A consulta de risco do Estoque exige
cobertura iniciando hoje e vínculo explícito do produto ao histórico.
A Fase 8 implementa alertas preventivos persistentes, reconciliação automática
e sob comando, com deduplicação e resolução somente após avaliação válida.
A Fase 9 acrescenta a tela de previsões na mesma aplicação Flask, usando
consulta por XMPP, filtros dentro da cobertura publicada, gráfico e métricas
por produto. Códigos sem associação ao catálogo também são consultáveis nessa
tela, sem alterar os vínculos exigidos pelas operações de estoque.
A Fase 10 implementa a coleta reprodutível de cenários controlados, metadados de
comunicação e avaliação histórica, com execução e fechamento finais registrados
no guia da fase. A suíte automatizada continua sendo um requisito separado.
Detalhes: [Fase 3](docs/FASE_3_SPADE_XMPP.md),
[Fase 4](docs/FASE_4_ESTOQUE.md), [Fase 5](docs/FASE_5_IMPORTACAO.md),
[Fase 6](docs/FASE_6_PREVISAO.md),
[Fase 7](docs/FASE_7_AGENTE_PREVISAO.md),
[Fase 8](docs/FASE_8_ALERTAS_PREVENTIVOS.md),
[Fase 9](docs/FASE_9_INTERFACE_WEB.md) e
[Fase 10](docs/FASE_10_VALIDACAO_EXPERIMENTAL.md).

Funcionalidades já implementadas:

- interface de atendimento;
- cinco clientes simulados no seed;
- histórico separado por cliente;
- persistência de mensagens;
- consulta de sabores;
- consulta de preços;
- consulta de disponibilidade;
- confirmação de compra;
- cancelamento de pedido;
- pedidos com múltiplos produtos;
- registro de vendas;
- atualização de estoque;
- entrada, saída e ajuste pelo saldo final, com histórico auditável;
- cadastro e controle manual de matérias-primas;
- quantidade inicial opcional de matéria-prima, com entrada auditável na mesma transação;
- mínimos configuráveis e alertas para saldo igual ou inferior ao mínimo;
- consulta e confirmação Atendimento → Estoque por XMPP real;
- consulta Estoque → Previsão por XMPP, por produto associado ou visão geral;
- avaliação temporal e previsão local por produto, com agregações diária, semanal e mensal;
- persistência de previsões e métricas, com publicação atômica e repetição segura;
- recálculo de previsão sob comando, com acompanhamento em segundo plano;
- alertas preventivos por saldo projetado, com horizonte configurável e atualização por XMPP;
- preservação dos alertas quando a previsão está indisponível ou desatualizada;
- repetição segura por chave de operação persistente;
- manutenção do pedido e da chave após falha de confirmação, com substituição ou cancelamento;
- tela principal de estoque em `/estoque` e consultas GET somente leitura;
- erros e sucessos próximos a cada formulário e resumo de alertas na navegação;
- tela de previsões em `/previsoes`, com códigos históricos, período e agrupamentos;
- gráfico local e comparação por produto com baselines;
- estimativas inteiras na interface, com total igual à soma das linhas arredondadas e decimais preservados no backend;
- atualização pela tela e acompanhamento de tarefa sem treinamento na thread HTTP;
- protocolo experimental local com cenários SPADE/XMPP e evidências privadas;
- tela de vendas;
- endpoints JSON para testes sem interface gráfica.

## Limitações atuais

- O agente usa regras simples por palavras-chave.
- Treinamento periódico não está ativado; a reconciliação dos alertas usa a
  execução de previsão disponível. O recálculo exige revisão histórica aprovada.
- A avaliação de risco por produto do estoque exige associação explícita por
  código externo. A tela de previsões permite consultar códigos sem essa associação.
- Ainda não há autenticação de usuários.
- Matérias-primas têm controle manual; abatimento por venda depende de receitas/BOM.
- Um pedido com múltiplos produtos gera múltiplos registros na tabela `vendas`.
- A interpretação de linguagem natural ainda é limitada a frases simples.
- Descrições históricas completas dependem de informação explícita; grupo ou
  categoria não substituem o nome do produto. Um cadastro descritivo separado
  permanece como proposta, sem novas associações ou produtos automáticos.
- A avaliação histórica compara erros em recortes temporais específicos; não
  demonstra significância estatística nem melhoria da operação da fábrica.

## Próximas evoluções possíveis

- melhorar interpretação de intenção;
- criar uma entidade formal de pedido com múltiplos itens;
- ampliar a validação com novos períodos e condições operacionais;
- exportar relatórios de vendas e atendimentos.
