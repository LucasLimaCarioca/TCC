# Fase 8 — Alertas preventivos baseados em previsão

O Agente de Estoque consulta o Agente de Previsão por XMPP e registra alertas
preventivos para produtos acabados. A projeção usa o saldo atual e a demanda
prevista no horizonte oficial configurado:

```text
saldo_projetado = estoque_atual - demanda_prevista_no_horizonte
risco_futuro = saldo_projetado < estoque_minimo
```

O limite é estrito: projeção igual ao mínimo não gera `risco_futuro`. A regra
anterior de estoque mínimo continua independente, com alerta quando o saldo
atual é igual ou inferior ao mínimo. Um produto pode apresentar os dois tipos
de alerta, pois eles descrevem condições diferentes.

Não há conversão de previsão de produtos em consumo de matérias-primas. Os
insumos continuam com movimentações manuais e alertas de estoque mínimo;
receitas/BOM permanecem fora desta fase.

## Configuração

| Configuração | Padrão | Efeito |
|---|---|---|
| `PREDICTIVE_ALERT_HORIZON_DAYS` | `7` | Horizonte de alerta, em dias de calendário, entre 1 e 366. |
| `PREDICTIVE_ALERTS_AUTO_ENABLED` | `1` | Habilita a reconciliação automática; `0` desabilita. |

As configurações podem ser definidas no Flask ou no ambiente. A configuração
Flask prevalece. Em testes, a atualização automática fica desabilitada por
padrão e o ambiente real não fornece essas configurações, evitando tarefas
inesperadas e dependência do computador do usuário. Um teste pode habilitar a
atualização explicitamente com dados fictícios.

O horizonte precisa caber na execução de previsão ativa. Informar um horizonte
maior não estende o artefato nem preenche dias ausentes com zeros. Consulte o
[guia da Fase 7](FASE_7_AGENTE_PREVISAO.md) para carregar uma execução e associar
os códigos externos aos produtos do catálogo.

`PREDICTIVE_ALERT_HORIZON_DAYS` é o horizonte oficial dos alertas persistentes.
Uma tentativa de criar, atualizar ou resolver um alerta com outro horizonte
retorna `ALERT_HORIZON_MISMATCH`, preservando o registro e seus horários.
Consultas de risco podem usar horizontes personalizados, sem efeitos colaterais.
Se a configuração oficial mudar, a avaliação anterior fica pendente até uma
reconciliação válida pelo novo horizonte oficial.

## Quando os alertas são atualizados

Com a atualização automática ativa, o Estoque solicita reavaliação depois de:

- venda ou movimentação de produto acabado;
- alteração do estoque mínimo de um produto;
- inicialização da previsão no runtime;
- publicação de uma nova execução após recálculo.

O aviso de publicação segue o contrato XMPP `previsao.publicada`, entre os
mesmos agentes. A reconciliação usa um único trabalhador e reúne solicitações
repetidas para o mesmo produto. A tarefa fica fora da Inbox que recebe respostas
XMPP e fora da transação da venda. Uma previsão indisponível não desfaz uma
venda já confirmada.

Também existe a ação explícita de atualizar um produto, pela tela, API ou CLI.
Não foi acrescentado treinamento periódico nem um cron para recalcular modelos.
A aplicação continua usando um único runtime local, conforme a Fase 3.

## Persistência e concorrência

A consulta à previsão acontece antes de abrir a transação de escrita. Ao
registrar o resultado, o serviço relê saldo, mínimo, código externo do produto
e execução ativa. Isso impede que uma resposta atrasada seja aplicada com um
saldo antigo ou com outra associação histórica.

Quantidades previstas e projeções usam `Decimal`. O serviço atualiza o alerta
ativo do produto enquanto o risco persiste, preservando seu identificador e
data de criação. Uma avaliação válida sem risco resolve o alerta, mantendo o
histórico. Se o risco reaparecer depois da resolução, uma nova ocorrência pode
ser registrada. A restrição de unicidade dos alertas preventivos ativos protege
também solicitações concorrentes.

Os metadados conservam a referência da previsão e da avaliação. Uma consulta
somente leitura pode indicar que o alerta precisa de nova avaliação, sem
alterar o registro nem seus horários. A migração incremental desta fase deve
ser aplicada antes de operar os novos alertas:

```bash
flask --app app.app:create_app db upgrade
```

O banco padrão permanece em `data/artifacts/sorvetes.db`. O comando de migração
cria um backup privado antes de atualizar uma instalação existente. A revisão
`0008_alertas_preventivos` preserva as linhas anteriores; duplicatas ativas ou
riscos legados de matérias-primas bloqueiam a migração com rollback para revisão,
sem correção automática dos registros.

Ao encerrar o runtime, o sinal de cancelamento também chega à persistência. Ele
é conferido depois de obter a transação e antes do commit, impedindo que uma
avaliação ainda não publicada substitua o alerta após a interrupção.

## Previsão indisponível ou desatualizada

O risco só é avaliado com cobertura iniciando hoje em `America/Manaus`, para o
produto correto, no horizonte solicitado e com execução válida. Uma previsão
com outra referência continua consultável para inspeção; ela não confirma o
risco do estoque atual.

Ausência de modelo, código não associado, histórico inelegível, erro de
artefato, timeout, desconexão e cobertura vencida impedem uma nova avaliação.
O retorno informa indisponibilidade com `risco=null` ou um erro tratado, como
`FORECAST_STALE`. O sistema preserva alertas anteriores e informa a pendência.
Falha de previsão não é demanda zero e não serve para resolver um alerta.

A tela distingue **Estoque mínimo** e **Risco futuro**. Quando a avaliação
registrada não é atual, apresenta **Avaliação pendente** e seu motivo. A tela
não exibe identificadores técnicos de execução ou hashes no fluxo do usuário.

## API e tela

`GET /api/estoque/risco/<produto_id>` continua somente leitura. Os parâmetros
opcionais são `horizonte_dias` e `granularidade` (`diaria`, `semanal` ou
`mensal`). Sem horizonte, usa a configuração dos alertas. Consultar risco
nunca cria nem resolve alertas.

`POST /api/estoque/alertas/preventivos/<produto_id>` solicita uma reconciliação
do produto. O corpo deve ser um objeto JSON vazio ou conter exclusivamente um
`horizonte_dias` inteiro igual ao horizonte oficial configurado:

```json
{"horizonte_dias": 7}
```

Campos adicionais, booleanos, valores nulos, texto numérico e números
fracionários são rejeitados com HTTP 400. Sem runtime, a operação retorna HTTP
503. Uma avaliação com previsão indisponível pode retornar HTTP 200 com o
estado desconhecido e `risco=null`; o cliente deve interpretar esse estado,
preservando a distinção entre indisponibilidade e ausência de risco.
Horizontes válidos mas diferentes do oficial também retornam HTTP 400, com
`ALERT_HORIZON_MISMATCH`, antes de solicitar a previsão.

Na tela `/estoque`, **Consultar risco** apenas apresenta a projeção.
**Atualizar alerta deste produto** é a ação de persistência e exige o horizonte
oficial; a consulta permite escolher outros horizontes. A sincronização anterior,
`POST /api/estoque/alertas/sincronizar`, continua dedicada aos alertas de
estoque mínimo dos produtos e matérias-primas.

## Comando local

O comando a seguir usa o runtime e a consulta Estoque → Previsão por XMPP.
Substitua `<id-do-catalogo>` pelo produto pretendido:

```bash
flask --app app.app:create_app previsao sincronizar-alerta \
  --produto-id '<id-do-catalogo>'

flask --app app.app:create_app previsao sincronizar-alerta \
  --produto-id '<id-do-catalogo>' --horizonte 7
```

Omitir `--horizonte` usa `PREDICTIVE_ALERT_HORIZON_DAYS`; um valor explícito deve
ser igual ao configurado. Como os demais comandos
que iniciam runtime temporário, execute com a aplicação web parada e com as
credenciais XMPP configuradas. O console informa apenas o caminho da resposta
privada, `data/reports/consulta-agente-<id>.json`; previsões e códigos reais
não são impressos.

## Validação e proteção dos dados

Os testes usam produtos e previsões fictícios. A cobertura inclui os limites
do mínimo e do horizonte, criação, atualização, resolução e repetição segura,
falhas de previsão, concorrência, troca da execução ativa, consultas somente
leitura, payloads HTTP, tela e exportação privada pela CLI.

O fluxo não lê o CSV original. Histórico, banco, previsões e relatórios reais
continuam restritos a `data/`, ignorados pelo Git. A auditoria de comunicação
mantém payloads redigidos. A suíte completa passou com **895 testes**, incluindo
**150 novos testes** desta fase, e 130 avisos de depreciação. A seleção SPADE/XMPP
usada no CI passou com **61 testes**. Dependências, JavaScript e proteção dos
dados também foram verificados.

A validação local com as previsões reais também foi aprovada: primeiro em cópia
privada, depois no banco principal com backup conferido e revisão
`0008_alertas_preventivos`. A inicialização automática e as atualizações por
XMPP seguiram a projeção decimal, sem duplicar alertas nas repetições. A tela e
as consultas GET preservaram os registros de alerta. Foram conferidas a
integridade/referências SQLite e a preservação de catálogo, saldos, vendas,
histórico, previsões e arquivos de origem. Os resultados por produto permanecem
somente nos relatórios privados de validação em `data/reports/`.

```bash
python -m pytest -q
node --check app/static/js/estoque.js
python3 scripts/check_private_data.py
git diff --check
```

A próxima fase é a Fase 9, dedicada à interface web final, incluindo a tela
completa de previsões. Os formulários desta fase apenas tornam a operação dos
alertas preventivos disponível na tela de estoque existente.
