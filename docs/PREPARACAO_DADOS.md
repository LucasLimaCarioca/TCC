# Preparação, proteção e desidentificação dos dados históricos

## Objetivo e uso

O pipeline transforma o histórico de itens em demanda diária por produto sem
propagar dados pessoais. É um comando separado do Flask e não altera o banco,
o atendimento ou os agentes. Não treina modelos nem importa vendas para SQLite.

A importação do dataset aprovado para `VendaHistorica` é um comando separado,
implementado na [Fase 5](FASE_5_IMPORTACAO.md). Recebe o relatório desta preparação,
confere o hash do CSV diário e preserva a política `dia_fonte`.

Na raiz do projeto, com as dependências instaladas:

```bash
source .venv/bin/activate
python -m scripts.prepare_history --input data/pedido.csv --date-policy dia_fonte
```

O arquivo correto é `data/pedido.csv`, com a descrição em
`data/descricao_campos.txt`. O arquivo anterior, `pedidos.csv`, tinha outro
schema e não serve para a demanda por produto. Nenhum dos arquivos locais,
nem os resultados de preparação, deve ser publicado.

## Contrato de entrada

CSV UTF-8 (BOM opcional), separado por vírgula, com cabeçalho sem nomes repetidos.
Os nomes de coluna são os do CSV, com maiúsculas e minúsculas preservadas.

| Campo | Tratamento |
|---|---|
| `pvNro` | Obrigatório, texto; usado apenas em memória para contar pedidos distintos |
| `proCodCli` | Obrigatório, texto; identidade do produto, preserva zeros à esquerda |
| `proNomeunificado` | Obrigatório, texto; verifica consistência do cadastro, não é exportado |
| `pvEmis` | Obrigatório, timestamp ISO 8601 com fuso explícito |
| `pvQtd` | Obrigatório, decimal finito não negativo |
| `gproNro`, `gproNm` | Opcionais; exportados como `codigo_grupo` e `categoria`, com validação de consistência |
| `pvPrcunt`, `pvPrctot` | Opcionais; quando preenchidos, exigem decimais finitos não negativos |
| Campos de clientes, vendedores e quaisquer colunas extras | Descartados da saída |

Espaços externos são removidos dos campos utilizados. O formato numérico usa
ponto decimal. A validação técnica admite até nove casas decimais e magnitude
até 10¹⁵, rejeitando NaN e infinito. Uma entrada fora desses limites gera erro
para revisão; não é arredondada silenciosamente.

## Regra de datas e agregação

A decisão do usuário é **preservar o dia escrito na fonte** (`dia_fonte`). Assim,
um timestamp fictício `2025-01-02T00:00:00Z` pertence ao dia `2025-01-02`.
Nenhuma conversão para o fuso de Manaus é feita nessa política.

O comando exige a política explicitamente. A alternativa `America/Manaus`
existe para uma eventual revisão autorizada, mas não é a política escolhida
para este histórico. Datas sem fuso ou inválidas bloqueiam a preparação.

Agrupar por dia e `proCodCli`, somando quantidades e contando `pvNro` distintos
dentro de cada combinação. Várias linhas diferentes do mesmo pedido/produto
somam suas quantidades e contam um único pedido. O número diário de pedidos
entre diferentes produtos não deve ser somado para estimar pedidos globais.

Não completar dias ausentes com zero nesta fase: a decisão depende do calendário
de operação e da preparação das séries. Quantidade zero explícita é mantida
e contabilizada no relatório; quantidades negativas exigem revisão e bloqueiam
a saída, sem assumir que representam devoluções.

## Saídas privadas

Cada execução recebe um identificador próprio e não sobrescreve resultados:

- `data/processed/preparacao-<id>/demanda_diaria.csv`;
- `data/reports/preparacao-<id>.json`.

A versão 2 do pipeline inclui o grupo comercial. O CSV possui seis colunas:

| Coluna | Conteúdo |
|---|---|
| `data` | Dia de emissão no formato YYYY-MM-DD |
| `codigo_produto` | Código textual, com zeros à esquerda |
| `codigo_grupo` | `gproNro` como texto, preservando zeros à esquerda |
| `categoria` | Nome comercial do grupo, recebido em `gproNm` |
| `quantidade` | Soma decimal das quantidades do produto naquele dia |
| `numero_pedidos` | Contagem de pedidos distintos do produto naquele dia |

A agregação continua por data e código de produto. Grupo e categoria são
metadados complementares, sem dividir ou somar novamente a demanda. Campos de
grupo ausentes continuam opcionais e ficam vazios na saída; quando houver um
único valor preenchido nas linhas do mesmo produto/dia, esse valor é usado.
Não há preenchimento a partir de outros dias ou produtos.

Nomes contraditórios para a categoria de um produto ou para o mesmo código de
grupo bloqueiam o dataset, inclusive entre produtos diferentes. Os conflitos
aparecem como contagens no relatório, sem copiar os nomes da fonte.

A ordenação é por data e código. Reexecuções com a mesma fonte e política
produzem o mesmo conteúdo de dataset, em novos caminhos locais.

O relatório registra versão, instante da execução, hashes SHA-256 da fonte e
do dataset aprovado, política de datas, contagens de linhas, campos ausentes,
erros por categoria, duplicatas exatas, códigos de produtos, intervalo de datas,
dias observados e inconsistências de produto/nome, produto/grupo,
produto/categoria e grupo/nome. Contagens e
intervalos referem-se às linhas válidas; um relatório bloqueado nunca indica
que esse subconjunto foi liberado para uso.

O relatório não copia linhas rejeitadas, nomes pessoais, identificadores de
pessoas, identificadores de pedidos ou textos arbitrários da fonte. Logs da CLI
mostram apenas status e caminhos gerados pelo programa. O hash SHA-256 identifica
o arquivo inteiro, não uma pessoa.

## Bloqueios e privacidade

O pipeline só lê fontes dentro de `data/`, ignoradas e não rastreadas pelo Git.
Valida também os destinos; rejeita links simbólicos e entradas com vínculos
físicos adicionais. Os arquivos gerados usam permissão 0600 e os novos diretórios
usam 0700. O original é somente lido. A CLI não permite selecionar destinos
públicos ou sobrescrever a fonte.

Linhas inválidas, CSV malformado, cabeçalho incompatível, duplicatas exatas e
metadados contraditórios de um mesmo produto geram relatório bloqueado, sem
dataset parcial. Duplicatas não são removidas automaticamente, pois podem exigir
uma decisão sobre a origem dos registros. Os códigos de saída são:

- `0`: dataset e relatório gerados;
- `1`: relatório gerado, dataset bloqueado por qualidade;
- `2`: erro de argumentos, proteção de caminhos ou leitura/gravação.

Todos os dados pessoais são eliminados. Não há necessidade de associação por
cliente ou vendedor nesta fase, portanto não se criam pseudônimos nem chaves
HMAC. Caso essa necessidade surja, ela exige decisão explícita e implementação
separada com HMAC-SHA256 e chave fora do repositório.

O schema permite apenas os campos definidos, incluindo o nome comercial da
categoria. Isso depende do significado correto das colunas de origem: não é um
detector genérico de informações pessoais inseridas indevidamente em códigos
ou nomes de categorias.

## Validação e manutenção

```bash
python -m pytest tests/test_preparacao_dados.py -q
python -m pytest -q
python3 scripts/check_private_data.py
```

Os testes usam apenas CSVs fictícios em repositórios temporários. Cobrem soma,
contagem distinta de pedidos, datas, zeros à esquerda, reexecução, erros,
colunas pessoais inesperadas, ausência de vazamentos nos relatórios/logs e
bloqueio de caminhos não ignorados, rastreados ou com links simbólicos.

A inspeção e a execução do histórico real ficam locais. Números, amostras,
gráficos e relatórios derivados do arquivo real não devem ser copiados para
este guia, para commits ou serviços externos. A próxima fase deve consumir
o dataset aprovado, sem reintroduzir os dados pessoais descartados.
