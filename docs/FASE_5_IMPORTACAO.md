# Fase 5 — Importação e validação do histórico

## Escopo

A Fase 5 persiste a demanda diária já preparada em `VendaHistorica` e permite
reconstruir as observações por código externo. O comando é separado da aplicação
web e não inicia o runtime SPADE. Não há treinamento, novas previsões, mudanças
de saldo nem associação automática de produtos.

A decisão aprovada na Fase 1 prevalece sobre o plano original: o importador
recebe o **dataset diário desidentificado**, não linhas brutas com pessoas.
Datas, quantidades e preços brutos continuam sendo validados exclusivamente pelo
[pipeline de preparação](PREPARACAO_DADOS.md). Clientes, vendedores, pedidos
individuais, preços e nomes brutos de produtos não são importados nesta etapa.

## Execução

Com as dependências instaladas e o banco na revisão `0006_integridade_operacoes`:

```bash
source .venv/bin/activate
python -m scripts.import_history --help
```

Use o caminho do relatório aprovado que o comando de preparação forneceu.
Substitua `<id>` pelo identificador local da execução:

```bash
python -m scripts.import_history --report 'data/reports/preparacao-<id>.json' --validar-apenas
python -m scripts.import_history --report 'data/reports/preparacao-<id>.json'
```

`--validar-apenas` consulta o catálogo e o histórico e gera um relatório privado,
sem INSERT, UPDATE, DELETE ou `BEGIN IMMEDIATE`. O comando de importação grava
no banco configurado por `create_app()`. Não cria tabelas, não executa seed e não
migra o banco implicitamente; bancos sem schema exigem `python create_db.py`.

Códigos de saída:

- `0`: validação ou importação concluída;
- `1`: relatório de qualidade bloqueado, sem novas observações persistidas;
- `2`: erro de entrada, proteção de caminhos, configuração ou persistência.

Os logs mostram apenas status e o caminho de relatório criado pelo programa.
Não exibem registros, códigos reais, categorias, hashes ou exceções internas.

## Contrato e proteção

O relatório de preparação deve estar em `data/reports/`, aprovado pela versão 2,
sem erros, com política **`dia_fonte`** e hashes SHA-256 válidos. O dataset referido
deve estar em `data/processed/`. O hash dos bytes do CSV deve ser idêntico ao hash
registrado na preparação. Não é necessário ler novamente o CSV original.

Entradas e saídas precisam estar ignoradas e fora do índice Git. Links simbólicos
e entradas com vínculos físicos adicionais são rejeitados. Relatórios novos
usam permissão 0600; diretórios criados pelo serviço usam 0700. Não há destinos
públicos configuráveis, uploads ou chaves HMAC. A fonte e o relatório original
não são reescritos. Essas validações conferem integridade e contrato; os nomes
comerciais devem continuar correspondendo ao significado definido na preparação.

O CSV UTF-8, com BOM opcional, exige exatamente as seis colunas, nesta ordem:

| Campo | Validação |
|---|---|
| `data` | Dia estrito `YYYY-MM-DD`, sem nova conversão de fuso |
| `codigo_produto` | Texto obrigatório, até 64 caracteres, zeros à esquerda preservados |
| `codigo_grupo` | Texto opcional, até 64 caracteres |
| `categoria` | Texto opcional, até 100 caracteres; grupo comercial da fonte |
| `quantidade` | Decimal finito não negativo, inferior a 10¹⁶ e até nove casas |
| `numero_pedidos` | Inteiro não negativo, até o limite de 64 bits do SQLite |

Espaços externos são aparados. Cabeçalhos extras, repetidos ou ausentes,
codificação inválida, datas/números inválidos, linhas malformadas e metadados
contraditórios bloqueiam toda a importação. Os relatórios contam tipos de erros
sem copiar os valores rejeitados.

## Repetição e atomicidade

`source_hash` é o SHA-256 de um JSON canônico da observação diária: data, código,
grupo, categoria, quantidade decimal normalizada e número de pedidos. Valores
como `1.25` e `1.250` representam a mesma observação. `dataset_hash` identifica
os bytes do primeiro dataset que inseriu a linha.

A chave de negócio é `(codigo_produto, data)`:

- observação idêntica no mesmo arquivo ou em nova importação: contabilizada sem
  duplicar a demanda;
- observação nova: inserida uma única vez;
- observação com quantidade, pedidos ou metadados diferentes: conflito para
  revisão, sem sobrescrever, somar ou importar parcialmente o restante.

Reimportações preservam IDs, hashes de origem e instantes de importação. A sessão
de escrita usa `BEGIN IMMEDIATE` antes de consultar o histórico; importações
concorrentes não aprovam a mesma observação como nova ao mesmo tempo. O relatório
é gravado antes do commit; falha nessa gravação reverte as inserções. Falha no
commit reverte a transação e remove o relatório de sucesso criado pela execução.

`Numeric(25,9)` no SQLite pode converter valores para ponto flutuante. O importador
relê as observações e compara suas assinaturas canônicas antes de confirmar.
Perda de precisão bloqueia a transação, sem arredondamento silencioso. O schema
existente e as revisões 0001–0006 permanecem intactos.

## Mapeamento e relatório privado

O vínculo com o catálogo usa exclusivamente `Produto.codigo_externo`. Produtos
legados sem código não recebem códigos inferidos por nome, sabor ou categoria.
Observações sem mapeamento permanecem no histórico por sua identidade externa;
o relatório lista os códigos a revisar. A categoria histórica não substitui
`Produto.categoria`, usado no atendimento.

Cada execução produz `data/reports/importacao-<id>.json`, com:

- caminhos privados, versão, política de datas e hashes de origem;
- linhas lidas, válidas e rejeitadas por formato;
- duplicatas no dataset, observações únicas, linhas já importadas, novas e
  efetivamente inseridas;
- conflitos com o banco e erros por tipo;
- intervalo temporal, número de produtos e códigos sem mapeamento;
- perfil por código: primeiro/último dia, dias observados, lacunas no intervalo,
  dias observados com quantidade zero e soma decimal das quantidades.

As contagens de formato não significam que um arquivo bloqueado foi parcialmente
importado. Lacunas são **dias sem observação**, não uma afirmação de demanda zero
nem de fechamento da fábrica. Datas ausentes não são preenchidas nesta fase.
Contagens de pedidos são preservadas por observação; somá-las entre produtos
não produz um número de pedidos únicos da fábrica.

## Reprodução da série

`app.services.importacao_service.serie_diaria(codigo_produto, inicio=None,
fim=None)` consulta somente o banco e retorna as seis propriedades preparadas,
ordenadas por data. Quantidades saem como texto decimal e pedidos como inteiros.
Filtros opcionais usam `YYYY-MM-DD`, incluindo ambos os limites.

A função não cria datas, não agrega novamente e não usa nome como identidade.
Não existe rota HTTP nem exportação pública do histórico; consumidores futuros
devem manter qualquer arquivo derivado em `data/`.

## Validação e fases seguintes

```bash
python -m pytest -q tests/test_importacao_historico.py
python -m pytest -q
python -m pip check
node --check app/static/js/estoque.js
python3 scripts/check_private_data.py
git diff --check
```

Os testes usam dados fictícios e bancos/repositórios temporários. Cobrem preparação
→ importação, dia comercial, zeros à esquerda, metadados, série ordenada,
repetição e sobreposições, concorrência, rollback, precisão, CLI sem vazamentos
e proteção de caminhos. A suíte completa também executa as regressões do TCC I
e os testes de comunicação SPADE/XMPP existentes.

Resultado da validação: **65 testes novos aprovados** e **366 aprovados** na
suíte completa. Os 128 avisos são os de `datetime.utcnow()` legado. `pip check`,
validação do JavaScript, proteção de dados e verificação do diff passaram.
O dataset local aprovado também foi validado, importado e reimportado em uma
cópia privada do banco dentro de `data/artifacts/`: repetição sem novas linhas,
preservação do catálogo/saldos/demais registros e verificações de integridade
e referências aprovadas. Essa validação não alterou o banco nem o CSV originais.

O relatório privado deve ser revisado antes de iniciar treinamento. Modelos,
preenchimento de lacunas, validação temporal e previsão ficam para a Fase 6;
o agente de Previsão permanece respondendo `NOT_IMPLEMENTED` até sua etapa.
