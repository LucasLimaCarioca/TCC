# TCC - Sistema de Atendimento para Sorveteria

Este repositório contém o protótipo desenvolvido para o Trabalho de Conclusão de Curso em Sistemas de Informação da UEA - EST.

O projeto simula um sistema de atendimento virtual para uma sorveteria, com foco em:

- atendimento conversacional;
- consulta de produtos, preços e disponibilidade;
- registro de vendas;
- persistência de histórico por cliente;
- confirmação de pedidos;
- suporte a pedidos com múltiplos produtos.

O protótipo atual utiliza Flask, SQLite e SQLAlchemy. O agente de atendimento ainda não usa SPADE; ele simula o comportamento de um agente por meio de uma classe Python com interpretação simples de intenções.

## Arquitetura

A arquitetura atual é simples e adequada para o estágio do TCC I:

```text
Interface Flask
   ↓
Rotas HTTP / Templates / APIs
   ↓
AtendimentoAgent
   ↓
Serviços de venda
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
AtendimentoAgent interpreta a intenção
   ↓
Agente consulta produtos/estoque no banco
   ↓
Se for pedido, salva contexto e pede confirmação
   ↓
Cliente confirma
   ↓
Sistema registra vendas e atualiza estoque
   ↓
Histórico da conversa é salvo por cliente
```

## Tecnologias utilizadas

### Principais no protótipo atual

- Python: linguagem principal.
- Flask: framework web usado para rotas, telas e APIs.
- Flask-SQLAlchemy: integração entre Flask e SQLAlchemy.
- SQLite: banco de dados local do protótipo.
- HTML/Jinja2: templates renderizados pelo Flask.
- CSS: estilização da interface.

### Dependências previstas para etapas futuras do TCC

O arquivo `requirements.txt` também inclui bibliotecas que fazem parte do escopo maior do TCC, mas não são centrais no protótipo atual:

- SPADE: framework para sistemas multiagentes, previsto para evolução futura.
- Pandas: manipulação de dados.
- Scikit-learn: modelos de previsão/demanda em fases futuras.
- Streamlit: alternativa de interface ou prototipação.
- OpenAI e python-dotenv: possíveis integrações futuras com APIs e variáveis de ambiente.

## Estrutura do projeto

```text
TCC/
├── app/
│   ├── agents/
│   │   └── atendimento_agent.py
│   ├── models/
│   │   ├── cliente.py
│   │   ├── contexto_conversa.py
│   │   ├── historico_conversa.py
│   │   ├── produto.py
│   │   └── venda.py
│   ├── routes/
│   │   ├── estoque_routes.py
│   │   ├── produto_routes.py
│   │   └── venda_routes.py
│   ├── services/
│   │   └── venda_service.py
│   ├── static/
│   │   └── css/
│   │       └── style.css
│   ├── templates/
│   │   ├── base.html
│   │   ├── produtos.html
│   │   ├── simulacao_venda.html
│   │   └── vendas.html
│   ├── app.py
│   └── database.py
├── instance/
│   └── sorvetes.db
├── create_db.py
├── seed.py
├── run.py
├── requirements.txt
└── README.md
```

## Função dos principais arquivos

### `run.py`

Arquivo de entrada da aplicação. Cria o app Flask e inicia o servidor de desenvolvimento.

```bash
.venv/bin/python run.py
```

### `app/app.py`

Define a função `create_app()`, responsável por:

- criar a aplicação Flask;
- configurar o banco SQLite;
- inicializar o SQLAlchemy;
- registrar os blueprints de atendimento, produtos e estoque.

### `app/database.py`

Cria o objeto global `db`, usado pelos modelos SQLAlchemy.

### `create_db.py`

Cria as tabelas no SQLite usando `db.create_all()`.

Também possui pequenas migrações manuais para adaptar bancos antigos do protótipo, por exemplo:

- adicionar colunas em `produtos`;
- adicionar `cliente_nome` em `vendas`;
- adicionar `itens_json` em `contextos_conversa`;
- migrar/remover a tabela antiga de estoque, caso exista.

### `seed.py`

Popula o banco com dados iniciais:

- três produtos;
- três clientes simulados.

O script é idempotente: pode ser executado mais de uma vez sem duplicar os dados principais.

## Modelos do banco

### `Produto`

Arquivo: `app/models/produto.py`

Representa os produtos vendidos pela sorveteria.

Campos principais:

- `id`
- `nome`
- `preco`
- `descricao`
- `quantidade_disponivel`
- `ativo`

O estoque simplificado fica dentro da própria tabela de produtos.

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

## Agente de atendimento

Arquivo: `app/agents/atendimento_agent.py`

O `AtendimentoAgent` é o protótipo do agente de atendimento.

Ele executa as seguintes funções:

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
- registro de pedido;
- confirmação;
- cancelamento.

Exemplos de mensagens aceitas:

```text
oi
quais sabores?
preco
tem chocolate?
quero 2 chocolate
quero 2 chocolate e 1 morango
sim
não
```

## Serviço de vendas

Arquivo: `app/services/venda_service.py`

Centraliza a regra de negócio de vendas.

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
- menu lateral com três clientes simulados;
- histórico separado por cliente;
- envio de mensagens ao agente.

### Produtos e Estoque

Rota:

```text
/produtos
```

Arquivo:

```text
app/routes/produto_routes.py
```

Template:

```text
app/templates/produtos.html
```

Tela unificada que mostra:

- produtos;
- descrições;
- preços;
- quantidade disponível;
- status do produto;
- status de estoque.

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

### Estoque

Rota:

```text
/estoque
```

Essa rota redireciona para `/produtos`, pois a tela visual de produtos e estoque foi unificada.

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
  -d '{"mensagem": "quero 2 chocolate e 1 morango", "cliente_nome": "Cliente Simulado"}'
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
pip install -r requirements.txt
```

### 4. Criar ou atualizar banco

```bash
python create_db.py
```

### 5. Inserir dados iniciais

```bash
python seed.py
```

### 6. Rodar aplicação

```bash
python run.py
```

A aplicação ficará disponível em:

```text
http://127.0.0.1:5000
```

## Estado atual do protótipo

Funcionalidades já implementadas:

- interface de atendimento;
- três clientes simulados;
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
- tela unificada de produtos e estoque;
- tela de vendas;
- endpoints JSON para testes sem interface gráfica.

## Limitações atuais

- O agente usa regras simples por palavras-chave.
- Ainda não há integração real com SPADE.
- Ainda não há autenticação de usuários.
- O estoque é simplificado e fica dentro da tabela `produtos`.
- Um pedido com múltiplos produtos gera múltiplos registros na tabela `vendas`.
- A interpretação de linguagem natural ainda é limitada a frases simples.

## Próximas evoluções possíveis

- melhorar interpretação de intenção;
- criar uma entidade formal de pedido com múltiplos itens;
- adicionar métricas para avaliação do protótipo;
- integrar SPADE em uma fase posterior;
- criar agente de previsão de demanda;
- criar agente de controle de estoque;
- exportar relatórios de vendas e atendimentos.
