# Fase 4 — Agente de Controle de Estoque

## Operações implementadas

`EstoqueAgent` agora responde às operações de disponibilidade e confirmação de
venda por XMPP. O Atendimento consulta o agente tanto ao informar saldos quanto
ao validar um pedido; a confirmação revalida os saldos na transação de escrita.
Com o runtime desativado, a mesma autoridade de estoque executa localmente.
Runtime ativo com falha não provoca troca silenciosa para execução local.

A tela principal `/estoque` permite cadastrar matérias-primas, registrar entradas,
saídas e ajustes, configurar mínimos, consultar alertas e ver as 100 últimas
movimentações. `/produtos` redireciona para `/estoque`; `/api/produtos` continua
como catálogo JSON. A navegação aponta para a rota principal de estoque.

Regras confirmadas pelo usuário:

- Ajuste informa o **saldo final desejado**. A movimentação guarda a diferença
  absoluta positiva e os dois saldos. Saldo final igual ao atual não cria uma
  movimentação de quantidade zero.
- Alerta imediato aparece quando **saldo <= estoque mínimo**, inclusive zero
  com mínimo zero. Reposição acima do mínimo resolve o alerta e preserva o
  histórico; a mesma condição mantém um único alerta ativo por item.
- Itens repetidos do mesmo produto são **somados antes de validar e vender**.
  Há uma venda e uma movimentação por produto agrupado.

Produtos usam unidades inteiras. Matérias-primas usam a unidade cadastrada e
valores decimais enviados como texto, com até nove casas. Não há consumo de
matéria-prima por venda nem receitas/BOM nesta fase. O cadastro inicia com saldo
zero; o saldo inicial é lançado por uma entrada auditável.
Códigos novos de matéria-prima são normalizados com `strip().upper()`;
cadastros equivalentes são rejeitados, incluindo códigos legados com outra
capitalização. Nomes e unidades são aparados antes de validar e persistir.

Consultas de saldos, matérias-primas, movimentações e alertas, assim como a tela
e `visao_estoque()`, são somente leitura. A consulta de risco mantém a auditoria
de mensagens XMPP, sem alterar saldos ou alertas. Alertas são atualizados
nas vendas, movimentações, alterações de mínimo e cadastros de matéria-prima.
Itens legados podem ser reconciliados explicitamente pelo botão **Sincronizar
alertas** ou por POST `/api/estoque/alertas/sincronizar`. Repetir a sincronização
mantém um único alerta ativo por item e preserva os registros resolvidos.

## Transação e repetição segura

`estoque_service.py` usa sessão própria e `BEGIN IMMEDIATE` no SQLite antes de
ler os saldos para escrita. Isso serializa operações concorrentes e impede que
duas vendas aprovem o mesmo saldo. Venda, alteração de saldo, movimentação,
atualização do alerta e recibo de operação são confirmados juntos. Qualquer
falha de gravação reverte todos esses efeitos.

`OperacaoEstoque` persiste UUID, tipo, hash canônico da solicitação e resultado
com identificadores. Mesma chave e mesmo pedido recuperam o resultado; chave
reutilizada com conteúdo diferente gera conflito. O UUID da operação permanece
estável em uma repetição; `thread` identifica cada troca XMPP e pode mudar.
A hash não é autorização para publicar informações ou dados históricos.
Nomes de cliente usam `strip()` antes da validação, persistência e hash. Valores
decimais equivalentes usam a mesma representação canônica no hash da operação.
Recibos antigos permanecem intactos; a normalização não reescreve hashes nem
registros de vendas existentes.

`MovimentacaoEstoque.venda_id` é uma FK única para auditar a origem da baixa.
Movimentações antigas permanecem com NULL, sem inventar associações.
`venda_service.py` mantém sua interface pública e delega ao estoque; não existe
uma segunda baixa no Atendimento ou nas rotas.

O contexto de confirmação guarda `operacao_id`. Falha de transporte mantém o
pedido e essa chave, permitindo repetir `sim` mesmo quando a venda já foi
gravada e sua resposta se perdeu. Falhas de negócio também mantêm o pedido e
a chave; falta de estoque informa o saldo atual e permite enviar outro pedido
ou cancelar. Somente sucesso ou cancelamento explícito remove o contexto.
Novo pedido válido recebe outra chave. Lista vazia é rejeitada no domínio com
`INVALID_INPUT`, sem criar venda, movimentação ou recibo.
Contextos legados recebem a chave ao confirmar. Cancelamento não estorna uma
operação que já tenha sido confirmada; estornos não fazem parte desta fase.

## Contratos e APIs

| Operação | Entrada | Resultado |
|---|---|---|
| XMPP `estoque.disponibilidade` | `items`: produto e quantidade inteira positiva. | `available`, itens agrupados com `solicitado`, `disponivel`, `ok`. |
| XMPP `estoque.baixa_venda` | `items`, `cliente_nome`, `operacao_id` UUID obrigatório. | `success`, IDs de `vendas` e `movimentacoes`. |
| GET `/api/estoque` | — | Produtos ativos, saldos e mínimos. |
| GET/POST `/api/estoque/materias-primas` | Cadastro: `codigo`, `nome`, `unidade_medida`, mínimo opcional. | Lista ou ID cadastrado. |
| GET/POST `/api/estoque/movimentacoes` | Movimento: `tipo_item`, `item_id`, `tipo_movimentacao`, `quantidade`, `motivo`. | Histórico ou resultado com saldo e ID. |
| PUT `/api/estoque/minimo` | `tipo_item`, `item_id`, `estoque_minimo`. | Mínimo configurado, alertas atualizados. |
| GET `/api/estoque/alertas` | — | Alertas ativos e resolvidos. |
| POST `/api/estoque/alertas/sincronizar` | — | Reconcilia explicitamente os alertas dos itens legados. |
| GET `/api/estoque/risco/<produto_id>` | `granularidade` e `horizonte_dias` opcionais; padrão diária/7 dias. | Consulta ao agente de Previsão. |

`tipo_item` aceita `produto` e `materia_prima`; movimentos manuais aceitam
`entrada`, `saida`, `ajuste`, com motivo obrigatório. Operações HTTP de venda
e movimento aceitam UUID no header `Idempotency-Key` ou no campo `operacao_id`.
Se ambos forem enviados, devem identificar a mesma operação; valores diferentes
geram 400 antes de qualquer alteração. Ambos os UUIDs são validados, inclusive
quando o outro é válido. Sem preferência silenciosa por header ou corpo.
Sem chave, uma chamada constitui uma nova operação; integrações devem reutilizar
a chave para repetir solicitações após timeout. O formulário de movimentação
mantém a chave enquanto repete o mesmo conteúdo na página.

Validação e estoque insuficiente retornam 400; conflitos de chave ou código
retornam 409, tanto nas movimentações quanto em `/api/vendas`.
Timeout/runtime indisponível retorna 503. Falhas XMPP preservam thread/ontology
e usam `failure` com código explícito, sem expor exceções internas.

## Previsão e fases seguintes

A consulta Estoque → Previsão é real. O agente de Previsão continua respondendo
`NOT_IMPLEMENTED`; a tela/API informa `previsao_indisponivel` e `risco=null`.
Nenhum valor previsto é inventado. O consumidor está preparado para uma resposta
válida do mesmo produto/granularidade e compara saldo projetado com mínimo.
Os testes desse consumidor usam respostas **fictícias**, sem treinar modelo.

Importação, treinamento e respostas de previsão ficam para as Fases 5–7.
Agendamento, persistência e resolução de alertas `risco_futuro` ficam para a
Fase 8. Esta implementação não lê nem importa `data/pedido.csv`.

## Migração e execução

Com a aplicação parada, use o ambiente virtual e migre antes de iniciar:

```bash
source .venv/bin/activate
python create_db.py
python -m flask --app run.py db current
python run.py
```

A revisão `0005_operacoes_estoque` adicionou o recibo de operação, o vínculo da
movimentação com venda e a chave opcional do contexto. A revisão incremental
`0006_integridade_operacoes` acrescenta CHECKs para `length(request_hash) = 64`
e `tipo IN ('venda', 'movimentacao')`, preservando os recibos e a revisão 0005.
Dados legados que violem essas restrições bloqueiam a migração, com backup e
rollback; nenhum recibo é apagado ou corrigido silenciosamente. O runner cria backup
privado em `data/artifacts/backups/` antes de atualizar banco existente e verifica
foreign keys. Não é necessário repetir o seed. Configure as senhas XMPP conforme
o [guia de infraestrutura](FASE_3_SPADE_XMPP.md). Para executar apenas localmente:
`TCC_SPADE_ENABLED=0 python run.py`.

O suporte transacional desta fase é para SQLite local. As características de
armazenamento `Numeric(25,9)` descritas na Fase 1 permanecem; não há garantia de
precisão decimal arbitrária. Os CHECKs de saldo não negativo e quantidade de
movimento positiva continuam ativos. Datas de movimentações são UTC.

## Validação

```bash
python -m pytest -q
python -m pytest -q --spade tests/test_regressao_tcc_i.py tests/test_atendimento_contexto.py tests/test_atendimento_caracterizacao.py tests/test_estoque_http.py tests/test_correcoes_estoque.py tests/test_estoque_service.py::test_resposta_perdida_mantem_contexto_para_repeticao_segura
node --check app/static/js/estoque.js
python -m pip check
python3 scripts/check_private_data.py
git diff --check
```

Toda a validação usa dados fictícios, independentes do CSV. Os testes cobrem
concorrência, itens repetidos, transação completa, saldo insuficiente, perda de
resposta local/XMPP, repetição persistente, alertas sem duplicatas, resolução,
controle decimal manual, telas/APIs e migração preservando campos legados.

Resultado após as correções: **301 testes aprovados** na suíte completa e
**61 aprovados** na execução adicional com SPADE ativo. Os 128 avisos da suíte completa vêm do uso
legado de `datetime.utcnow()`. A verificação no Chrome cobriu cadastro, entrada
decimal, mínimo no limite, ajuste para saldo zero e histórico, sem exceções
JavaScript. `pip check`, validação do JavaScript, proteção de dados e verificação
do diff passaram. A rota principal `/estoque` e o botão de sincronização explícita
também foram verificados no Chrome, com dados fictícios e sem exceções JavaScript.

O banco local foi migrado inicialmente para `0005_operacoes_estoque` após validar uma cópia
privada. Todos os campos legados foram comparados e preservados; novas chaves
de contexto/venda permanecem NULL nos registros antigos. Backup e cópia ficam
somente em `data/artifacts/`, ignorados. A adoção do banco legado preserva suas
diferenças anteriores de schema (unicidade do nome de produto e nulabilidade
dos campos de venda); esta fase não reconstrói esses dados. Em banco novo, o
schema migrado é equivalente aos modelos, conforme o teste de migração.

A revisão `0006_integridade_operacoes` foi validada primeiro em uma nova cópia
privada e aplicada ao banco local com backup. A comparação integral dos registros
confirmou a preservação dos recibos e dos demais dados; os novos CHECKs,
`integrity_check` e as foreign keys passaram. A revisão 0005 não foi modificada.
Os testes também comprovam backup e rollback quando um recibo legado inválido
impede a atualização para 0006.
