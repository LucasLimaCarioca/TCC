# Correções após a validação manual do TCC II

Estas alterações atendem à revisão manual de Atendimento, Estoque e Previsões.
A aplicação continua em Flask, SQLite e na topologia SPADE/XMPP
Atendimento ↔ Estoque ↔ Previsão. Os contratos de mensagens, os cálculos dos
modelos e a persistência dos alertas preventivos permanecem preservados.
Não há nova migration nem alteração de migrations já aplicadas neste conjunto.

## Atendimento sem divulgação de saldos

A consulta continua usando os saldos internos da autoridade de Estoque.
A resposta ao cliente informa apenas se o produto está disponível ou
indisponível. A listagem geral também apresenta esse estado por produto.

Quando um pedido excede o saldo, o Atendimento informa que a quantidade
solicitada não está disponível e sugere uma quantidade menor ou outro produto.
A confirmação ainda revalida o pedido na transação de venda. Se essa
revalidação rejeitar o pedido, a mensagem não repete o saldo numérico recebido
do Estoque, e o contexto e sua chave permanecem disponíveis para repetição
segura, substituição ou cancelamento.

A restrição vale para disponibilidade, validação inicial, confirmação, métodos
públicos da fachada local e respostas recebidas por XMPP. Preços, quantidades
pedidas e recibos continuam sendo apresentados. O tamanho da embalagem no nome,
como 10L, não é saldo. O cliente interno mantém seu comportamento anterior por
padrão; o Atendimento solicita a apresentação sem saldos nas recusas de estoque
insuficiente. Os DTOs internos e as mensagens administrativas não são limitados.

Ao reabrir uma conversa, os saldos de padrões conhecidos de mensagens antigas
de estoque são ocultados somente na projeção exibida ao cliente. O texto
persistido continua intacto. A substituição é específica para os formatos antigos de
disponibilidade e estoque insuficiente; não elimina números globalmente,
não modifica preços nem altera quantidades dos pedidos.

## Mensagens nos formulários de Estoque

Cada formulário possui sua própria área de notificação próxima dos controles.
Erros recebem destaque visual, identificação acessível e texto compreensível;
os valores preenchidos permanecem no formulário. Uma saída superior ao saldo
continua sendo rejeitada com o HTTP correto, sem permitir saldo negativo.

Durante o envio, a interface evita submissões concorrentes do mesmo formulário.
Uma operação válida produz mensagem de sucesso. Quando é necessário recarregar
a página para mostrar o saldo atualizado, a aplicação transporta somente um
código fixo de sucesso em sessionStorage, consumido na nova página. Não são
armazenados saldos, nomes, quantidades, motivos, valores dos campos ou respostas
da API nesse recurso.

O retorno de erro ou sucesso não depende apenas de cor e é anunciado por
tecnologia assistiva. Falha de comunicação não representa confirmação da operação.

## Mínimo de matéria-prima: passo inteiro e entrada decimal

O mínimo continua aceitando entrada manual de até nove casas decimais.
A interface aceita vírgula ou ponto e envia o decimal textual com ponto.
A validação do backend continua sendo a autoridade sobre valores válidos.

Os botões de aumentar e diminuir variam o valor em uma unidade. Um mínimo
fictício de 1,234567891 passa a 2,234567891 ao aumentar. O controle mantém a
parte fracionária e respeita o limite não negativo. A aritmética decimal da
interface preserva a nona casa, sem depender da conversão para ponto flutuante.

O campo permanece editável por teclado e usa modo de entrada decimal em
dispositivos móveis. Os botões possuem identificação acessível. O passo dos
botões não limita a entrada manual a valores inteiros.

## Cadastro com quantidade inicial opcional

POST /api/estoque/materias-primas aceita quantidade_inicial opcional.
Omiti-lo mantém o cadastro anterior com saldo zero. Zero não gera uma
movimentação de quantidade zero.

Exemplo inteiramente fictício de formato:

    {
      "codigo": "INSUMO-FICTICIO",
      "nome": "Insumo fictício",
      "unidade_medida": "kg",
      "estoque_minimo": "1.234567891",
      "quantidade_inicial": "2.345678912"
    }

A quantidade inicial deve ser zero ou positiva, compatível com a precisão
já aceita para matérias-primas. O valor usa a unidade cadastrada, sem conversão
automática de unidades.

Um valor positivo gera movimentação de entrada com motivo identificável de
cadastro inicial. Cadastro, saldo, movimentação e eventual alerta de estoque
mínimo são confirmados na mesma transação. Falha na gravação, movimentação,
atualização do alerta ou commit reverte o cadastro inteiro. Valores cuja
precisão não seja preservada pelo armazenamento são rejeitados, sem
arredondamento silencioso.

A prevenção de duplicação combina o bloqueio do envio pendente na interface
com unicidade e normalização do código na transação. Nova solicitação com o
mesmo código retorna DUPLICATE_MATERIAL, HTTP 409, e não credita novamente a
quantidade inicial. Essa rejeição não é um segundo recibo de sucesso. Não foi
criada uma nova chave de operação para esse cadastro.

O cadastro inicial não introduz receitas/BOM nem baixa automática de
matérias-primas por vendas.

## Indicador de alertas na navegação

O indicador consulta GET /api/estoque/alertas e conta somente alertas ativos.
O resumo distingue alertas imediatos de estoque mínimo e alertas preventivos
de risco futuro. Um preventivo com avaliação desatualizada é identificado;
continuar ativo não significa que sua projeção ainda esteja vigente.

O resumo permite consultar novamente a contagem e acessar a seção de alertas
do Estoque. Uma falha de consulta aparece como indisponibilidade, não como zero
alertas. Quando já existe uma contagem, ela é conservada com indicação explícita
de que a atualização não foi confirmada. A consulta ocorre ao carregar a página,
abrir o resumo ou solicitar atualização; retornos de foco/visibilidade respeitam
um intervalo mínimo entre tentativas automáticas. O resumo contém contagens,
sem copiar mensagens ou saldos dos alertas. O GET não cria, resolve ou atualiza alertas, nem modifica saldos.
Não há WebSocket ou servidor adicional.

## Horizonte oficial do alerta preventivo

O formulário de atualização exibe o horizonte de PREDICTIVE_ALERT_HORIZON_DAYS,
sem campo editável. A requisição envia o objeto vazio {}; a resolução e a
validação do horizonte permanecem no backend.

O formulário separado de consulta de risco mantém o horizonte personalizado
e continua somente leitura. A regra da Fase 8 permanece: persistência em
horizonte divergente é rejeitada e não pode encerrar um alerta existente.

O alerta imediato continua usando saldo igual ou inferior ao mínimo.
O preventivo continua usando saldo projetado estritamente inferior ao mínimo
e os decimais originais da previsão.

## Datas do gráfico de previsão

Uma consulta diária com até sete dias desenha uma identificação para cada
data. O espaçamento acompanha o número de períodos e a largura disponível.
Em intervalos maiores, os rótulos usam distribuição adaptativa, conservando
os pontos e os valores da série.

Em telas estreitas, a área permite rolagem horizontal para evitar
sobreposição. Datas abreviadas mantêm identificação completa acessível;
intervalos que cruzam anos não perdem essa distinção. A tabela continua
apresentando todos os períodos. O ajuste não muda previsões ou agregações.

## Quantidades inteiras na apresentação da previsão

A interface usa arredondamento **HALF_UP** para quantidades não negativas:
frações inferiores a 0,5 ficam no inteiro inferior; frações iguais ou
superiores a 0,5 ficam no superior. A operação parte do decimal textual e usa
inteiros de precisão arbitrária para evitar perda na conversão de valores grandes.

Cada período exibido é arredondado uma vez. Gráfico, título do ponto e linha
correspondente usam esse mesmo inteiro. O total apresentado é a soma dos
inteiros das linhas exibidas, mantendo consistência entre tabela e total.

Esse total pode diferir do arredondamento direto do total decimal publicado.
Também pode variar entre agrupamentos, pois períodos diferentes são
arredondados, ou da soma de produtos arredondados separadamente. Exemplo
fictício: dois dias com 0,4 unidade aparecem como 0 + 0
na visão diária; a agregação de 0,8 aparece como 1 na visão agrupada. Isso não
representa uma nova previsão nem modifica a quantidade persistida.

A tela informa que são estimativas arredondadas para apresentação.
Backend, publicações, artefatos e métricas MAE, RMSE, WAPE e sMAPE permanecem
inalterados. Alertas preventivos continuam usando os decimais originais.

## Nomes e códigos históricos

O rótulo associado usa Produto.nome somente com vínculo explícito por
Produto.codigo_externo. A categoria histórica é identificada como categoria;
ela não é apresentada como descrição completa do produto.

Códigos sem descrição cadastrada continuam consultáveis por sua identidade
textual, inclusive zeros à esquerda, com indicação da ausência. Nenhum produto
operacional é criado para preencher esse rótulo.

Na investigação local, o contrato do histórico diário preparado contém código,
grupo e categoria, mas não o nome comercial completo. Foram encontrados códigos
sem vínculo ao catálogo; os nomes dos itens associados na amostra inspecionada
não explicaram o problema. O serviço do painel já separa nome associado de
metadados históricos, e a apresentação passou a tornar essa distinção explícita.
A inspeção foi somente leitura e sua evidência permanece em relatório privado
em `data/reports/`, sem reproduzir registros nesta documentação. Não se completam
nomes por suposição nem se criam correspondências por nome, categoria ou
equivalência numérica.

Como o contrato preparado não contém a descrição completa, a proposta é um
**cadastro explícito de descrições dos códigos históricos**, separado do
catálogo operacional. A chave seria o código textual original e a descrição
seria fornecida e revisada pelo operador, sem criar vínculo de estoque.

Esse cadastro é uma proposta, não uma funcionalidade implementada nestas
correções. Uma futura tabela exigirá nova revisão incremental de schema se
aprovada. Registros históricos e migrations aplicadas não são modificados
para corrigir apresentação.

## Componentes alterados

| Área | Componentes |
|---|---|
| Respostas e projeção do histórico | response_builder.py, atendimento_service.py, estoque_client.py, venda_routes.py |
| Cadastro inicial e transação | estoque_service.py, estoque_routes.py |
| Formulários e controles decimais | estoque.html, estoque.js e estilos compartilhados |
| Resumo de alertas | base.html, notificacoes_estoque.js e estilos compartilhados |
| Datas, rótulos e arredondamento | previsoes.js, previsoes.html |
| Regressões | Testes sintéticos e mensagens dos cenários TCC I em app/experiments/functional.py |
| Automação e documentação | .github/workflows/ci.yml, README.md, docs/FASE_9_INTERFACE_WEB.md e este documento |

Novos testes: `tests/test_atendimento_privacidade_saldos.py`,
`tests/test_materia_prima_inicial.py`, `tests/test_estoque_interface.py` e
`tests/js/{estoque,notificacoes_estoque,previsoes}.test.cjs`. Os testes de
caracterização, contexto, runtime e regressão existentes tiveram as mensagens
esperadas ajustadas à política de disponibilidade sem saldos.

## Validação e limites

Os testes cobrem respostas novas, contexto legado, transações, rejeições,
repetição segura e projeção histórica sem reescrever registros. O cadastro
inicial exige zero, valor positivo, precisão, duplicação e rollback integral.

A conferência visual deve usar somente dados fictícios, em desktop e tela
móvel: erros próximos ao formulário com campos preservados, sucesso após
recarga, controles decimais, resumo de alertas, sete datas e consistência
entre gráfico, linhas e total.

Comandos de verificação:

    python -m pytest -q
    python -m pytest -q --spade \
      tests/test_regressao_tcc_i.py \
      tests/test_atendimento_contexto.py \
      tests/test_atendimento_caracterizacao.py \
      tests/test_estoque_http.py \
      tests/test_correcoes_estoque.py \
      tests/test_estoque_service.py::test_resposta_perdida_mantem_contexto_para_repeticao_segura
    python -m pip check
    node --check app/static/js/estoque.js
    node --check app/static/js/previsoes.js
    node --check app/static/js/notificacoes_estoque.js
    node --test tests/js/*.test.cjs
    python3 scripts/check_private_data.py
    git diff --check

Validação local em 30/10/2026: a suíte completa aprovou **1.189 testes**,
com 183 avisos de depreciação originados em dependências. Os **40 testes
JavaScript** passaram. `pip check`, as três verificações `node --check`, a
proteção de caminhos privados no índice Git e `git diff --check` passaram.
Essas execuções locais não representam uma execução remota do GitHub Actions.

A seleção obrigatória com `--spade` aprovou **61 testes**, com 103 avisos de
depreciação nas mesmas dependências. Os agentes e o servidor XMPP locais
foram exercitados com dados fictícios. As verificações automatizadas solicitadas
passaram; a arquitetura e as migrations aplicadas foram preservadas.

A conferência visual real em desktop e celular **permanece pendente**: o
navegador de automação retornou "The browser access host is unavailable" nesta
sessão. O servidor temporário com dados fictícios foi encerrado. Os testes
automatizados de DOM e contratos HTTP passaram, mas não substituem essa
conferência visual. Nenhum dado histórico real foi usado nesse ambiente.

A documentação pública descreve comportamento e procedimentos. Relatórios,
métricas, rótulos e outros resultados reais permanecem exclusivamente em data/.
Os testes não usam dados reais. Estas correções não iniciam funcionalidades
fora do escopo da revisão manual.

