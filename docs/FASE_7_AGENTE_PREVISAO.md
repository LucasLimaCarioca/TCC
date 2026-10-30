# Fase 7 — Agente de Previsão de Demanda

O `PrevisaoAgent` integra o motor da [Fase 6](FASE_6_PREVISAO.md) ao transporte
SPADE/XMPP local. O Estoque consulta uma execução publicada, solicita um
recálculo e acompanha seu resultado. Previsões, métricas e referências dos
artefatos são persistidas no SQLite privado, sem alterar o histórico importado
ou os saldos de estoque.

Alertas preventivos persistentes continuam na Fase 8. A tela completa de
previsões permanece na Fase 9. Atualização periódica é opcional no plano e não
está ativada nesta implementação.

## Componentes

| Arquivo | Responsabilidade |
|---|---|
| `app/agents/previsao_agent.py` | Inicialização, consultas, agendamento e estado dos recálculos. |
| `app/services/previsao_service.py` | Validação dos artefatos, seleção de produto/horizonte e chamada do motor. |
| `app/services/previsao_repository.py` | Validação das previsões e métricas e publicação transacional. |
| `app/models/previsao_execucao.py` | Execução, manifesto, métricas, resultado exato e indicador de publicação ativa. |
| `app/models/previsao_demanda.py` | Linhas por código histórico, granularidade e período, com cobertura e quantidade exata. |
| `app/agents/protocol.py` | Contratos JSON de consulta, atualização e acompanhamento. |
| `app/forecast_commands.py` | Comandos Flask para carregar artefatos e operar o agente por XMPP. |
| `migrations/versions/0007_previsoes_execucoes.py` | Migração incremental que preserva as previsões legadas. |

O runtime continua com um loop dedicado e os mesmos três agentes:

```text
Flask / comando local → AgentGateway
                            |
                      EstoqueAgent
                            |
                         XMPP local
                            |
                      PrevisaoAgent
                            |
                      PrevisaoService
                       /           \
             artefatos da Fase 6   SQLite privado
```

Atendimento não se comunica diretamente com Previsão. Não há novo servidor
externo nem serviço de previsão separado.

## Configuração e inicialização

Atualize o esquema antes de usar a persistência da Fase 7:

```bash
flask --app app.app:create_app db upgrade
```

O banco padrão continua em `data/artifacts/sorvetes.db`. A migração acrescenta
execuções e amplia as linhas de previsão sem apagar vendas, histórico ou
previsões legadas.

| Variável | Uso |
|---|---|
| `FORECAST_MANIFEST` | Opcional. Manifesto privado concluído que será validado e carregado na inicialização. |
| `FORECAST_REVIEW` | Revisão histórica aprovada, exigida para recalcular. Não aponta para o CSV bruto. |
| `FORECAST_START` | Início do histórico usado pelo motor; padrão `2022-01-01`, conforme a decisão sobre o período da pandemia. |
| `FORECAST_CALENDAR_OVERRIDES` | Opcional. Exceções operacionais privadas para o calendário. |

Sem `FORECAST_MANIFEST`, o agente recupera a execução ativa já persistida no
banco. Sem execução ativa, retorna `MODEL_UNAVAILABLE`; os outros agentes
continuam funcionando. Erros de inicialização da previsão também são tratados
sem apresentar resultados inventados.

As variáveis são lidas do ambiente ou da configuração Flask. `python run.py`
carrega o `.env` privado da pasta do launcher antes de iniciar os agentes,
preservando variáveis já exportadas. `.env.example` é apenas uma referência.
Configure as senhas XMPP conforme o [guia da Fase 3](FASE_3_SPADE_XMPP.md).

Na carga inicial são conferidos os hashes dos arquivos relacionados pelo
manifesto, incluindo modelos, relatório, séries e previsões. Os modelos pickle
são lidos como bytes para conferir integridade; **não são desserializados nem
executados pelo agente**. Cada consulta volta a conferir o manifesto e os JSON
de previsão e relatório consumidos, sem reler todos os arquivos binários. O
manifesto também deve coincidir com a referência e o hash persistidos.

## Carregar uma execução existente

Depois de substituir `<id>` pelo identificador local de uma execução concluída:

```bash
flask --app app.app:create_app previsao carregar \
  --manifest 'data/artifacts/previsao-<id>/manifest.json'
```

Esse comando não inicia XMPP nem treina modelos. Valida os artefatos e publica
a execução no banco. A execução deve possuir manifesto concluído da Fase 6 e
seus arquivos devem permanecer nas pastas privadas previstas.

## Identidade dos produtos e visão geral

A consulta por produto recebe o `Produto.id` do catálogo e resolve o código
histórico exclusivamente por `Produto.codigo_externo`. Não associa registros
por ID numérico, nome, sabor, grupo ou categoria. Um produto sem associação explícita retorna
`PRODUCT_UNMAPPED`; um código associado sem previsão nessa execução retorna
`HISTORY_NOT_FOUND`.

Após a nova rodada de validação, a consulta operacional também exige revisão
explícita válida do vínculo. Uma associação existente não é, por si só, prova
de correspondência comercial. Vínculos legados e suspeitos ficam pendentes até
conferência pelo operador e não habilitam alertas preventivos silenciosamente.

Todos os códigos presentes na execução são preservados na persistência. Os
que ainda não possuem associação ao catálogo conservam `produto_id` nulo nas
linhas históricas de previsão. Isso não cria produtos nem altera o catálogo.

O fluxo recomendado para associar ou rever códigos é a tela `/catalogacao`,
que exige confirmação explícita, identificação do operador, motivo e dados
comerciais conferidos. Ela permite usar um produto existente livre ou criar um
novo cadastro com ativação explícita. Alterações são auditáveis e reversíveis
quanto ao vínculo; não reescrevem histórico, saldos ou previsões publicadas.

O código textual, inclusive zeros à esquerda, é preservado. Não é válida a
suposição de que o ID interno e o código histórico correspondam numericamente.
Associações feitas anteriormente sob essa hipótese são mantidas para revisão,
sem correção ou aprovação automática. Consulte
[a nova validação de catalogação](NOVA_VALIDACAO_CATALOGACAO.md).

`produto_id=null` solicita a visão geral. Ela soma todos os produtos da execução,
inclusive códigos ainda não associados ao catálogo. Como na Fase 6, esse total
representa quantidades comerciais e não uma conversão para litros, peso ou
faturamento.

## Períodos, horizonte e precisão

O horizonte é medido em dias de calendário, entre 1 e 366, limitado à cobertura
da execução ativa. A consulta começa **no dia seguinte ao corte do artefato**;
não muda silenciosamente de referência conforme o relógio avança.

Para consultar sete dias de um artefato mais longo, o serviço recorta primeiro
os sete valores diários e depois calcula semanas ou meses. Assim, a soma
semanal/mensal inclui somente os dias solicitados. Cada período informa limites
nominais, cobertura efetiva e `parcial` quando não cobre a semana ou o mês
inteiro. O total geral deriva das mesmas previsões diárias dos produtos.

Quantidades atravessam o contrato JSON como **texto decimal canônico**. Somas
usam `Decimal`, sem conversão para ponto flutuante no transporte. Por exemplo,
um resultado fictício de `2.5` é transmitido como `"2.5"`.

A consulta de risco do Estoque só calcula saldo projetado quando a cobertura
começa hoje em `America/Manaus`. Para uma execução com outra referência,
retorna `previsao_desatualizada` e `risco=null`; o artefato continua consultável
para inspeção do período original. Essa verificação não cria alertas
preventivos persistentes.

## Persistência e publicação

`PrevisaoExecucao` armazena identificação da execução, caminho/hash do manifesto,
corte, horizonte, versão do motor, métricas e snapshot JSON com os valores
decimais textuais. As métricas mantêm avaliação temporal, baselines, seleção e
comparação no teste final. Não são copiadas repetidamente para cada período.

`PrevisaoDemanda` normaliza os produtos por frequência e período. Além do campo
numérico legado, `quantidade_exata` conserva o decimal textual para evitar perda
de precisão na representação numérica do SQLite. As somas exatas e a visão
geral ficam preservadas no snapshot da execução.

A publicação usa uma transação própria com `BEGIN IMMEDIATE`. Todas as linhas
precisam ser validadas e inseridas antes da troca da execução ativa, que é a
última alteração da publicação. Uma falha provoca rollback e conserva a
execução anterior. Um índice único permite apenas uma execução ativa.

Repetir a mesma execução, hash e conteúdo não duplica períodos. Um conteúdo
divergente com o mesmo identificador é rejeitado. Recarregar uma execução já
substituída também não a reativa: a repetição é um no-op, preservando a publicação
atual. Identificador de execução e versão do motor são informações distintas.

## Contratos XMPP

Toda mensagem conserva `performative`, `ontology`,
`language=application/json`, `thread` UUID e corpo validado. A resposta usa o
mesmo `thread` da solicitação.

| Ontology | Solicitação Estoque → Previsão | Resposta |
|---|---|---|
| `previsao.consulta` | `produto_id` positivo ou nulo, `granularidade`, `horizonte_dias`. | Períodos, cobertura, total, corte, modelo, geração, versão e execução. |
| `previsao.atualizar` | `data_corte` e `horizonte_dias`. | Confirmação rápida com `job_id` e `status=em_execucao`. |
| `previsao.status` | `job_id` UUID. | `em_execucao`, `concluido` ou `falhou`, com execução ou erro tratado. |

Falhas usam `performative=failure` e `code`, `message`, `details`. Entre os
casos tratados estão ausência de execução, produto inexistente ou sem vínculo,
histórico ausente, horizonte indisponível, artefato inválido e falha de
persistência. O Estoque informa indisponibilidade e não calcula risco com uma
demanda artificial.

## Consultar pelo agente

Os comandos `consultar` e `atualizar` iniciam um runtime local temporário. Execute
com a aplicação web parada, pois o launcher web e a CLI não compartilham o
mesmo runtime nem as portas XMPP. As três senhas XMPP devem estar definidas no
ambiente.

```bash
flask --app app.app:create_app previsao consultar --horizonte 7

flask --app app.app:create_app previsao consultar \
  --produto-id 1 --granularidade semanal --horizonte 7
```

O identificador `1` é apenas exemplo de um item do catálogo local. Omitir
`--produto-id` consulta o geral. A CLI solicita a previsão pelo caminho
Estoque → Previsão em XMPP e grava o resultado somente em
`data/reports/consulta-agente-<id>.json`. O console informa o caminho privado,
sem imprimir previsões, códigos ou métricas reais.

## Atualizar sob comando

Configure `FORECAST_REVIEW` para a revisão aprovada e substitua `YYYY-MM-DD`
pelo último dia completo autorizado por essa revisão:

```bash
flask --app app.app:create_app previsao atualizar \
  --corte YYYY-MM-DD --horizonte 28 --limite-segundos 3600
```

O corte deve ser anterior a hoje e não pode superar a referência da revisão.
Sem novos dados aprovados, o recálculo não preenche dias posteriores como se
fossem observações confirmadas. O motor mantém a cadeia de preparação/revisão,
o início aprovado, o calendário e a avaliação temporal da Fase 6.

O agente confirma o recebimento antes do treinamento. O trabalho é executado
em segundo plano fora do Inbox; consultas podem continuar usando a execução
ativa anterior. Um segundo recálculo simultâneo recebe `FORECAST_BUSY`.
Somente depois de concluir os artefatos e a persistência a nova execução passa
a atender consultas.

O estado dos trabalhos existe em memória no processo do agente, com retenção
limitada. Reiniciar o runtime perde esse acompanhamento; as execuções já
publicadas permanecem no banco. `JOB_NOT_FOUND` informa um trabalho ausente
na instância atual.

Ao encerrar o runtime, é solicitada interrupção cooperativa. O motor verifica
esse sinal nos pontos de progresso entre produtos e antes da ativação; uma
chamada de ajuste de modelo em andamento não é interrompida instantaneamente.
O encerramento pode, portanto, aguardar essa chamada terminar. O limite da CLI
controla o acompanhamento do trabalho, sem aumentar o timeout das consultas.
Artefatos incompletos não substituem a publicação anterior.

## Proteção e validação

Originais, séries, relatórios, modelos, exportações e banco permanecem locais
em `data/`, fora do Git. A auditoria XMPP persiste somente metadados; payloads
são registrados como `{"redacted": true}`. As mensagens de erro não reproduzem
registros, conteúdo de arquivos ou exceções do driver. O agente não lê o CSV
original nem executa modelos pickle recebidos de fora.

Os testes usam dados fictícios para verificar contrato, horizontes e períodos,
precisão decimal, associação explícita ao catálogo, persistência e rollback,
repetição segura, falhas tratadas, comunicação XMPP, recálculo em segundo plano
e preservação dos fluxos existentes. A suíte completa passou com **739 testes**
e 129 avisos de depreciação. Depois dos últimos ajustes de encerramento, os
**133 testes** de protocolos, agentes, serviço e integração foram novamente
aprovados. Uma verificação adicional de isolamento das variáveis de ambiente
passou junto com o serviço e a integração (**34 testes**); testes não herdam
caminhos de dados reais definidos no shell do usuário.

A seleção de regressão SPADE/XMPP usada no GitHub Actions também passou:
**61 testes**, incluindo os oito casos do TCC I e os fluxos de estoque.
`pip check`, verificação do JavaScript, proteção dos dados e `git diff --check`
foram aprovados.

A validação local com os artefatos reais foi aprovada primeiro em uma cópia
privada e depois no banco principal, com backup conferido. Foram verificadas
migração, integridade/referências SQLite, persistência idempotente, consultas
gerais nas três granularidades e consultas dos produtos explicitamente
associados por XMPP, usando os timeouts padrão. Saldos, vendas, histórico,
fontes e artefatos foram preservados; no catálogo, somente os vínculos
confirmados foram acrescentados. Resultados e pendências de associação estão
em relatórios privados sob `data/reports/`.

```bash
python -m pytest -q
python3 scripts/check_private_data.py
git diff --check
```
