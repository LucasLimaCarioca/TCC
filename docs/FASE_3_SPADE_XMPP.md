# Fase 3 — Infraestrutura SPADE/XMPP

Este documento registra o escopo e os resultados da Fase 3. A disponibilidade,
a venda/baixa transacional e a repetição segura por chave persistente já foram
implementadas na [Fase 4](FASE_4_ESTOQUE.md); `NOT_IMPLEMENTED` permanece apenas
para o agente de Previsão. A revisão atual do banco é `0006_integridade_operacoes`.

## Escopo implementado

Os três agentes são subclasses de `spade.agent.Agent`, com conexão ao servidor
XMPP embutido PyJabber em localhost. A topologia admite Atendimento ↔ Estoque e
Estoque ↔ Previsão. Atendimento ↔ Previsão é rejeitada.

O Flask continua síncrono. `AgentGateway` submete corrotinas ao event loop dedicado
de `AgentRuntime` usando `asyncio.run_coroutine_threadsafe`, com prazo explícito.
O chat chama `AtendimentoSPADEAgent.responder`; esse agente executa o serviço da
Fase 2 em um worker, criando um contexto Flask/sessão ORM próprios. A entrada do
Flask é uma chamada local ao runtime. As mensagens entre agentes passam por XMPP.

O agente de Atendimento mantém o diálogo, as vendas e o histórico do protótipo.
A classe `AtendimentoAgent` permanece como fachada local para compatibilidade
e execução com o runtime desativado. Estoque e Previsão respondem a `infra.ping`.
Os contratos de disponibilidade, baixa e previsão já estão definidos, mas essas
operações retornam `failure/NOT_IMPLEMENTED` até suas fases de negócio. A consulta
e a baixa atuais do diálogo continuam no serviço de vendas, até a Fase 4.

## Arquivos e responsabilidades

| Arquivo | Responsabilidade |
|---|---|
| `app/agents/config.py` | Portas, timeouts e senhas do ambiente/configuração. |
| `app/agents/runtime.py` | Lifecycle, loop dedicado, exclusividade por processo, health e auditoria. |
| `app/agents/xmpp_server.py` | Worker do servidor embutido, TLS efêmero e adaptações para PyJabber 0.4.5. |
| `app/agents/base_agent.py` | Behavior receptor, RPC, correlação e transporte XMPP. |
| `app/agents/atendimento_agent.py` | Agente SPADE de Atendimento e fachada compatível. |
| `app/agents/estoque_agent.py` | Agente SPADE de Estoque. |
| `app/agents/previsao_agent.py` | Agente SPADE de Previsão. |
| `app/agents/protocol.py` | Contratos, validação JSON e metadados. |
| `app/agents/gateway.py` | Interface síncrona usada pelo Flask. |
| `run.py` | Inicialização explícita e encerramento; reloader desativado. |

O servidor roda em um processo filho gerenciado. Isso permite que os handlers
de sinais do PyJabber rodem na thread principal desse processo e isola seus
singletons. O runtime encerra agentes, servidor, loop e workers; inicialização
incompleta faz limpeza. Portas ocupadas geram erro controlado. Há no máximo um
runtime por processo; `start` e `stop` são idempotentes e há teste de reinício.

Importar `run.py` ou chamar `create_app()` não inicia sockets ou threads.
`python run.py` inicia o runtime por padrão. `flask --app run.py run` usa apenas o
factory e o atendimento local; não ativa automaticamente SPADE. Para a execução
multiagente desta fase, use `python run.py`. O modelo suportado é uma instância
local; execução com vários workers WSGI não está configurada nesta fase.

## Contratos e falhas

Toda mensagem entre agentes tem `performative` (`request`, `inform`, `failure`),
`ontology`, `language=application/json`, `thread` UUID e corpo JSON validado.

| Ontology | Request | Comportamento nesta fase |
|---|---|---|
| `infra.ping` | `{}` | Retorna agente e `status=ok`. |
| `estoque.disponibilidade` | Lista `items` com produto/quantidade positivos. | `NOT_IMPLEMENTED`. |
| `estoque.baixa_venda` | Cliente e lista `items`. | `NOT_IMPLEMENTED`. |
| `previsao.consulta` | Produto, granularidade e horizonte positivos. | `NOT_IMPLEMENTED`. |

As respostas preservam `thread` e `ontology`. Uma resposta só resolve a chamada
que corresponde à thread, ontology e remetente esperados. Mensagem inválida com
correlação recebe `INVALID_MESSAGE`; ontology de outro agente recebe
`UNSUPPORTED_ONTOLOGY`. Mensagens sem correlação segura ou de remetentes fora da
topologia são descartadas. Timeout remove a chamada pendente, e resposta tardia
não resolve outra chamada. Não há repetição automática de operações de venda.

Falhas usam `{code, message, details}`. Runtime/agente indisponível e timeout
viram HTTP 503 no chat, sem expor exceções ou payloads ao cliente.

O timeout limita a espera HTTP. Uma operação síncrona de banco já iniciada em
worker pode concluir após essa espera expirar; cancelar uma Future não desfaz
commits. A infraestrutura não garante entrega exatamente uma vez nem implementa
idempotência de operações de negócio. Esses aspectos devem ser tratados junto
da transação de venda/estoque na Fase 4.

## Servidor local e compatibilidade

SPADE 4.1.4 fornece um servidor embutido PyJabber; a implementação usa a mesma
biblioteca com lifecycle explícito para a integração com Flask. Referência:
[documentação oficial do servidor embutido](https://spade-mas.readthedocs.io/en/develop/usage.html).

O `Container` do SPADE entrega diretamente mensagens entre agentes registrados
no mesmo processo. `XMPPTransport` usa o envio XMPP do behavior para que os testes
exercitem o servidor e seus sockets. Esse adaptador depende da API da versão
fixada, incluindo `_xmpp_send`.

O servidor escuta somente em loopback, nas portas de cliente/servidor configuradas.
Seu banco de autenticação fica em memória; certificados e chave TLS efêmeros
ficam em diretório temporário privado, com arquivos 0600, e são removidos ao parar.
O certificado é próprio de localhost e a verificação de cadeia pública do cliente
SPADE fica desativada nesse cenário local. Não há serviço HTTP de uploads.

O worker aplica três adaptações restritas ao PyJabber 0.4.5:

- executor de dois threads para o cálculo de hashes de autenticação;
- PluginManager mínimo para roster/ping, pois a versão instancia o plugin de
  upload mesmo quando desabilitado;
- reset do parser XML antes da troca STARTTLS: o reset tardio da versão original
  podia descartar o primeiro header cifrado e bloquear a conexão.

Essas adaptações não alteram arquivos das dependências. A versão PyJabber e as
dependências diretamente importadas são fixadas em `requirements.txt`. Atualizar
SPADE/PyJabber exige revalidar os adaptadores e os testes reais de comunicação.

## Configuração e execução

Com o ambiente virtual ativado e o banco já migrado da Fase 1:

```bash
python -m pip install -r requirements-dev.txt
export XMPP_ATENDIMENTO_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export XMPP_ESTOQUE_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export XMPP_PREVISAO_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python run.py
```

Esses comandos geram senhas locais sem exibi-las. Também é possível defini-las
na configuração da aplicação. `.env.example` lista as variáveis; `python run.py`
não carrega esse arquivo automaticamente. Não há senha padrão embutida.

| Variável | Padrão |
|---|---|
| `TCC_SPADE_ENABLED` | `1` no launcher; `0` usa a fachada local. |
| `XMPP_ATENDIMENTO_PASSWORD`, `XMPP_ESTOQUE_PASSWORD`, `XMPP_PREVISAO_PASSWORD` | Obrigatórias com SPADE ativo. |
| `XMPP_CLIENT_PORT`, `XMPP_SERVER_PORT` | `5222`, `5269`. |
| `AGENT_STARTUP_TIMEOUT` | 20 segundos. |
| `AGENT_REQUEST_TIMEOUT` | 5 segundos. |
| `AGENT_SHUTDOWN_TIMEOUT` | 10 segundos. |
| `FLASK_DEBUG` | `0`; mesmo com `1`, reloader desativado. |

Não há migração nova nem necessidade de repetir o seed. Para consultar o estado:

```bash
curl http://127.0.0.1:5000/api/agentes/status
```

`running` informa o estado do runtime/servidor, e `agents` informa a conexão de
cada agente. Com o runtime desativado, o endpoint informa `transport=local`.

## Privacidade e validação

O CSV histórico não é lido nesta fase. Os testes usam bancos e catálogos fictícios.
`LogMensagemAgente` guarda remetente/destinatário fixos, metadados reconhecidos,
UUID, status e `payload={redacted: true}`. Nenhum corpo de mensagem, cliente,
dataset ou quantidade histórica é copiado para esses logs. Traces de payload do
SPADE ficam desativados, e o logger do PyJabber não imprime stanzas/autenticação.

```bash
python -m pytest -q
python -m pytest tests/test_regressao_tcc_i.py tests/test_atendimento_contexto.py --spade -q
python -m pip check
python3 scripts/check_private_data.py
```

Os testes de infraestrutura executam XMPP real em portas locais temporárias;
precisam de permissão para abrir sockets. O teste não é substituído por mock nem
ignorado quando a rede local está bloqueada. O segundo comando executa os oito
casos do TCC I e os quatro de contexto através do gateway/runtime real.

As coberturas novas incluem os quatro sentidos da topologia, contratos inválidos,
correlação concorrente e resposta incompatível, agente/servidor offline, timeout,
porta ocupada, cleanup, exclusividade, idempotência e reinício do runtime.

Resultado final: **234 testes aprovados**, incluindo 35 casos novos da Fase 3.
A execução adicional com `--spade` aprovou os **12 casos de regressão/contexto**.
Os 85 avisos da suíte completa são do uso legado de `datetime.utcnow()`.
`pip check` não encontrou dependências incompatíveis; o verificador de privacidade
não encontrou caminhos restritos no índice Git; `git diff --check` passou.
