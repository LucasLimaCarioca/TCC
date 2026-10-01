# Fase 1 — Modelos e migrações

## Critérios de entrada

A Fase 0 e a preparação/desidentificação foram verificadas: 96 testes aprovados,
oito cenários do TCC I preservados e nenhum caminho restrito no índice Git.
Os comportamentos conhecidos do atendimento descritos em `BASELINE_FASE_0.md`
continuam documentados; não impedem esta etapa de schema e não foram alterados.
As regras de diálogo, venda e ajuste de estoque devem ser tratadas nas suas fases.

## Modelos

| Modelo | Decisões e restrições |
|---|---|
| `Produto` | `codigo_externo` textual, único quando preenchido; produtos legados ficam com NULL. `estoque_minimo` inteiro não negativo, inicialmente zero. Saldo atual e categoria comercial do TCC I preservados. |
| `MateriaPrima` | Código único, unidade explícita, saldo/mínimo decimais não negativos e estado ativo. |
| `MovimentacaoEstoque` | Exatamente um produto ou matéria-prima, coerente com `tipo_item`; entrada, saída, ajuste ou venda; saldos não negativos; quantidade, motivo e instante UTC. |
| `AlertaEstoque` | Exatamente um item; tipo `estoque_minimo` ou `risco_futuro`; nível, mensagem, criação, resolução e estado ativo. |
| `VendaHistorica` | Uma observação diária por produto externo, com grupo/categoria, quantidade, número de pedidos e hashes. Sem clientes, vendedores ou pedidos individuais. |
| `PrevisaoDemanda` | Produto existente, granularidade diária/semanal/mensal, período válido, quantidade não negativa, versão/modelo, instante e métricas JSON. |
| `LogMensagemAgente` | Metadados de comunicação, correlação (`thread`), payload JSON e status. Ainda não há mensagens SPADE em execução. |

As novas quantidades usam `Numeric(25, 9)`; o saldo de produto acabado permanece
inteiro por compatibilidade. SQLite mantém suas características de armazenamento
numérico; essa definição não transforma SQLite em um banco de precisão decimal
arbitrária. Timestamps novos usam UTC, sem timezone no armazenamento SQLite,
seguindo a convenção dos modelos existentes sem usar `datetime.utcnow()`.

A exclusividade entre produto e matéria-prima é garantida por CHECKs, e as
referências por foreign keys, habilitadas em cada conexão SQLite da aplicação.
Regras operacionais de movimentação, geração/resolução automática de alertas e
registro automático de movimentações de venda ficam para a Fase 4.

## Histórico diário e privacidade

O usuário confirmou a adaptação do plano antigo: persistir o dataset diário
preparado, não as linhas brutas contendo pessoas. Campos de `VendaHistorica`:

- `codigo_produto`, `data`, `codigo_grupo`, `categoria`;
- `quantidade`, `numero_pedidos`;
- `source_hash`, `dataset_hash`, `importado_em`.

`source_hash` reserva o SHA-256 de uma linha canônica do dataset preparado;
`dataset_hash` identifica o dataset que forneceu a linha. A importação que
calculará esses hashes será implementada posteriormente. Há unicidade do hash
da linha e de `(codigo_produto, data)`: sobreposição com valores diferentes não
pode somar silenciosamente a mesma demanda, devendo ser reconciliada pela futura
rotina de importação. Não há vínculo obrigatório com o catálogo local antes de
realizar o mapeamento por `Produto.codigo_externo`.

A categoria do dataset histórico é o grupo da fonte. Ela não substitui
automaticamente `Produto.categoria`, usado no diálogo do TCC I para os formatos
comerciais. Nenhum código externo é inferido a partir dos nomes atuais.

Esta fase apenas cria tabelas vazias. Nenhum histórico real foi importado, nenhum
modelo foi treinado e nenhum log real de agente foi gerado. Os emissores de logs
futuros devem retirar dados pessoais do payload antes de persistir mensagens.

## Migrações

O Alembic 1.18.4, já presente no ambiente, agora é dependência direta fixada.
As revisões não importam os modelos atuais: o schema de cada versão fica congelado.

1. `0001_baseline`: cria as cinco tabelas originais em banco vazio ou adota um
   banco legado. Aplica as antigas adições de colunas de `create_db.py`. Se houver
   a tabela legada `estoque`, transfere seu saldo uma única vez e preserva a
   tabela como evidência, sem removê-la.
2. `0002_modelos_tccii`: adiciona os campos do produto, as seis tabelas novas,
   índices, restrições e referências. Não recria nem apaga tabelas de vendas,
   histórico de conversas, clientes ou contextos.

Com a aplicação parada para evitar escritas concorrentes:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python create_db.py
python -m flask --app run.py db current
python run.py
```

`python -m flask --app run.py db upgrade` é equivalente a `create_db.py`.
O banco padrão continua sendo `instance/sorvetes.db`. Não execute `seed.py`
novamente para migrar: o seed redefine os saldos da demonstração.

O runner migra exclusivamente SQLite nesta fase. Usa a API de backup do SQLite
para gerar uma cópia consistente em `data/artifacts/backups/banco-<id>.db`, com
permissão 0600, antes de atualizar um banco com tabelas existentes. O destino
precisa estar ignorado e fora do índice Git. Se o backup falhar, a migração não
começa. Bancos vazios não precisam de backup; bancos já na revisão atual não
são alterados nem geram cópias repetidas.

As alterações usam `BEGIN IMMEDIATE` e verificam as foreign keys antes do commit.
Em caso de erro, DDL e dados são revertidos. O erro público não inclui registros.
A revisão fica em `alembic_version`. Não há downgrade destrutivo automático:
para recuperação, pare a aplicação e restaure a cópia local apropriada; isso
retorna também os dados ao estado daquela cópia.

Bancos legados com referências inválidas ou um schema diferente dos suportados
podem exigir revisão; o procedimento deve falhar preservando o original e seu
backup, nunca apagar registros para contornar o problema.

## Testes

```bash
python -m pytest tests/test_migrations.py tests/test_modelos_tccii.py -q
python -m pytest -q
python3 scripts/check_private_data.py
```

Os testes usam dados fictícios. Incluem schema equivalente entre migrações e
modelos, banco vazio, banco TCC I populado, formato antigo com tabela `estoque`,
idempotência, backup, referências inválidas, rollback de DDL e dados, interface
CLI e compatibilidade de `create_db.py`. Os modelos são testados sobre um banco
migrado, incluindo códigos com zeros, unicidade, CHECKs, FKs e persistência JSON.

A verificação adicional em cópia do banco local deve comparar todos os campos
legados sem imprimir registros. Backups e cópias usados nessa verificação ficam
exclusivamente em `data/`, ignorados pelo Git.

## Resultado da execução

A Fase 1 foi concluída com **133 testes aprovados** (37 novos), incluindo os
96 testes anteriores. Os 40 avisos da execução são de `datetime.utcnow()` em
modelos legados; os modelos novos usam a função UTC descrita acima.

A migração foi validada primeiro em uma cópia privada do banco local. Em seguida,
o banco da aplicação foi atualizado para `0002_modelos_tccii`, com backup
automático. A comparação de todos os campos legados confirmou a preservação
dos registros; as referências e as telas/APIs existentes passaram na verificação.
As novas tabelas permanecem vazias. Não houve importação de dados reais.

`pip check` não encontrou dependências incompatíveis. O verificador de privacidade
não encontrou caminhos restritos no índice Git. Backups e cópias locais continuam
sob `data/artifacts/`, ignorados pelo Git. A próxima etapa é a Fase 2, de
refatoração do Atendimento, preservando os testes e revisando os comportamentos
conhecidos antes de mudar suas regras.
