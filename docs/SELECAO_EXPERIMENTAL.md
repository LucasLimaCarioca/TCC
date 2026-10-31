# Catálogo e seleção experimental da previsão

O objetivo desta adaptação é manter os 11 produtos fictícios existentes e usar
somente históricos reais de produtos comercialmente equivalentes para treinar e
avaliar o Agente de Previsão. O estoque, os preços de demonstração e as vendas
operacionais são simulados; as observações históricas preparadas mantêm sua
origem real. Essa distinção acompanha as associações e as novas execuções.

## Componentes reutilizados

A catalogação do commit `78e7f6b` já fornece confirmação explícita, identificação
do operador, motivo, versões de estado, idempotência, auditoria e reversão.
Esses mecanismos continuam em uso. A topologia Atendimento ↔ Estoque ↔ Previsão
e o caminho HTTP → autoridade de Estoque → persistência são preservados.

O motor já filtra linhas por códigos externos. A adaptação fornece esse filtro
a partir da seleção de associações válidas, em vez de presumir que todo o
histórico representa o catálogo fictício. A construção temporal das séries,
validação, teste final, modelos candidatos e baselines são reutilizados.

Preparação, desidentificação e importação permanecem integrais. `VendaHistorica`
continua guardando observações por código externo textual. `Produto.id` continua
sendo a chave interna, e `Produto.codigo_externo` continua registrando o vínculo
explícito. Nenhuma quantidade, código ou observação histórica é substituída por
um ID de produto simulado.

## Vínculo experimental e compatibilidade

O vínculo tem finalidade explícita: operacional legado ou experimental. A
finalidade experimental não é inferida de nomes, categorias, do campo
`demonstracao` ou de semelhança numérica entre o ID interno e o código comercial.
Os códigos originais, inclusive zeros à esquerda, são conservados.

O modo experimental permite somente escolher um cadastro existente elegível
entre os 11 produtos fictícios definidos. Não cria produtos a partir do CSV.
A elegibilidade do destino identifica o perfil de demonstração; ela não prova
equivalência com um código histórico. Essa equivalência depende de conferência
comercial explícita do operador.

A interface pede a descrição comercial histórica conferida, sua embalagem e as
unidades da quantidade histórica e simulada. O operador confirma separadamente
a identidade comercial, a embalagem, a unidade e o uso do produto fictício.
Não há conversão automática de unidades. Compatibilidade negativa, ausente ou
não demonstrada não autoriza incluir o código na previsão.

Grupo ou categoria histórica não são tratados como nome completo do produto.
Se a fonte preparada não contém detalhes suficientes, é necessário consultar
uma referência comercial apropriada. O sistema não inventa uma descrição,
equivalência, preço, saldo ou fator de conversão.

Vínculos anteriores permanecem preservados e não se tornam experimentais ou
selecionados automaticamente. Revisões perdem validade quando mudam os dados
que sustentavam sua identidade ou os metadados históricos conferidos. Alterar
saldo ou mínimo continua sendo operação de estoque, sem mudar a identidade.

## Fluxo na interface

1. Abra `/catalogacao` e selecione o código histórico textual completo.
2. Para um código sem vínculo, use **Associar a um cadastro existente**. Para
   um vínculo atual, use **Confirmar a revisão do vínculo atual**. Trocar o
   destino exige a operação explícita de transferência.
3. Escolha a finalidade **Experimental — histórico real, operação simulada**.
   Confira o destino entre os produtos fictícios elegíveis.
4. Informe as evidências de identidade, embalagem e unidade. Marque as quatro
   confirmações somente depois da conferência comercial.
5. Marque **Incluir esta associação experimental no próximo treinamento e na
   previsão geral** quando quiser utilizar o código. Deixe desmarcado para
   manter a associação fora das próximas execuções.
6. Informe operador e motivo, confirme a operação e registre. Alterar dados
   depois de confirmar exige uma nova confirmação.
7. Solicite uma nova previsão para publicar o conjunto selecionado. A seleção
   atual não reescreve uma execução anterior.

Ao revisar um vínculo experimental existente, a tela recupera suas evidências
e sua situação de inclusão. As caixas de compatibilidade e a confirmação final
nunca são aprovadas automaticamente. O operador pode desmarcar a inclusão,
reconfirmar e registrar a nova seleção sem retirar o vínculo ou apagar dados.

O modo operacional legado permanece explicitamente separado para manutenção
das operações anteriores. Ele não é uma autorização de participação no
experimento. O formulário experimental não oferece criação de produtos e
rejeita esse tipo de payload mesmo se o controle de interface for adulterado.

## Seleção e rastreabilidade da execução

Cada nova execução experimental conserva o conjunto de códigos, os IDs internos
associados, as versões das revisões e a assinatura da seleção. Treinamento,
previsões individuais e previsão geral usam somente esse conjunto. Seleção
vazia ou inválida não significa treinar com todo o histórico.

A referência da seleção permite reconhecer uma mudança de vínculo, revisão ou
participação durante um recálculo ou uma consulta. Uma previsão de uma seleção
anterior não passa a representar silenciosamente a nova seleção. O operador
precisa gerar uma execução correspondente ao conjunto atual antes de usá-la
para avaliar o estoque simulado.

Uma nova seleção gera nova execução identificável, com seus próprios artefatos
e relatórios privados. Execuções e métricas anteriores são preservadas, assim
como os valores originais das vendas históricas. Os relatórios distinguem
**histórico real** de **estoque e vendas operacionais simulados**; não apresentam
o resultado do experimento como validação de um estoque real da fábrica.

## Avaliação temporal e alertas

A seleção de associações decide quais produtos participam; não decide o modelo
a partir de observações de teste. As janelas temporais continuam separando
treino, validação e teste. As observações alvo de cada avaliação são posteriores
ao corte de treino da respectiva janela. A avaliação conserva MAE, RMSE, WAPE,
sMAPE e as comparações com os baselines.

O relatório experimental acrescenta uma avaliação independente pós-corte.
Quando a revisão aprovada já cobre observações posteriores ao corte escolhido,
compara a previsão publicada e os baselines com esses dias abertos, até o limite
do horizonte e da cobertura revisada. Essas observações não participam do
treino, da seleção do modelo ou do ajuste final. Cobertura parcial é identificada;
sem observações posteriores disponíveis, o relatório informa “não avaliado”.
As métricas existentes do teste final anterior ao corte permanecem preservadas.

O Agente exige seleção válida em todo recálculo. Para treinamento offline,
exporte o snapshot confirmado e informe-o explicitamente ao script:

```bash
.venv/bin/python -m flask --app app.app:create_app previsao exportar-selecao
.venv/bin/python scripts/forecast_history.py train \
  --review data/reports/REVISAO_APROVADA.json \
  --experimental-selection data/reports/SNAPSHOT_EXPORTADO.json \
  --cutoff AAAA-MM-DD --start 2022-01-01 --horizon 28
```

Substitua os caminhos e a data pelos valores locais efetivamente confirmados.
O script recusa treinamento sem snapshot; ativar uma execução experimental
também exige que a seleção ainda corresponda aos vínculos auditados do banco.
O motor interno conserva compatibilidade para validar e consultar publicações
legadas, sem reescrever seus resultados.

O Estoque consulta a previsão pelo código externo explicitamente associado ao
produto fictício. O saldo usado no risco e nos alertas é o saldo simulado desse
cadastro. Associação experimental pendente, incompatível, não selecionada ou
que não corresponda à publicação não autoriza calcular um risco aparentemente
válido. Alertas anteriores são preservados quando não há uma previsão válida;
isso não representa demanda zero.

O horizonte persistente continua sendo `PREDICTIVE_ALERT_HORIZON_DAYS`.
Consultas personalizadas continuam somente leitura. A comunicação segue pelo
Estoque ↔ Previsão via SPADE/XMPP, sem novo agente ou alteração da topologia.

## Migração, privacidade e validação

A adaptação usa uma migration incremental. Revisões aplicadas anteriormente
não são alteradas nem revertidas. A atualização do banco deve usar o mecanismo
existente com backup privado. Ela não apaga observações, corrige vínculos,
seleciona códigos ou ativa produtos por conta própria.

Todos os históricos, derivados, relatórios, cópias e artefatos reais continuam
restritos a `data/`, fora do Git. Testes automatizados usam exclusivamente dados
fictícios e independentes do histórico real. As validações incluem Python,
JavaScript, SPADE/XMPP, dependências, sintaxe e proteção de privacidade.

Validação local concluída em 30/10/2026:

- Suíte Python completa: **1.377 testes aprovados**, incluindo as novas
  integrações de catálogo, treinamento, publicação, consulta e alertas por XMPP.
- Seleção do CI com `--spade`: **61 testes aprovados**.
- Suíte JavaScript completa: **76 testes aprovados**.
- Dependências (`pip check`), sintaxe dos quatro scripts JavaScript, verificação
  de caminhos privados no índice Git e `git diff --check`: aprovados.

Os testes Python emitiram 183 avisos de depreciação na suíte completa e 103 na
seleção SPADE, originados em dependências; não houve falhas. Estes resultados
correspondem às verificações locais do CI, não a uma execução remota do GitHub Actions.

A migration incremental `0010_associacoes_experimentais` foi aplicada ao banco
local com backup privado. A comparação por hashes de todas as colunas anteriores
confirmou a preservação dos registros, inclusive catálogo, saldos, vínculos,
histórico, auditorias e publicações. O comprovante permanece em `data/reports/`.
Nenhum vínculo legado foi convertido ou selecionado automaticamente.

Os principais componentes alterados são:

| Responsabilidade | Componentes |
|---|---|
| Finalidade, compatibilidade, seleção e auditoria | `catalogacao_historica.py`, `catalogacao_service.py`, migration `0010` |
| Filtro, identificação e avaliação pós-corte | `forecasting/experimental.py`, `forecasting/service.py`, `previsao_repository.py` |
| Publicação e conciliação com o estoque | `previsao_service.py`, `experimento_previsao_service.py`, `alerta_preventivo_service.py`, `estoque_service.py` |
| Transporte e interface | `estoque_agent.py`, `protocol.py`, templates e JavaScript de Catalogação, Estoque e Previsões |
| Execução offline com seleção explícita | `forecast_commands.py`, `scripts/forecast_history.py` |

Os novos testes estão em `test_associacoes_experimentais.py`,
`test_forecasting_experimental.py`, `test_experimento_protocol.py` e
`test_previsao_experimental_integration.py`, além dos acréscimos JavaScript e
das adaptações dos cenários existentes para seleção explicitamente confirmada.

As equivalências reais e suas evidências comerciais precisam ser fornecidas e
confirmadas pelo operador. A implementação desse fluxo não realiza associações
reais, não seleciona históricos reais automaticamente e não exige apagar os
produtos fictícios existentes.
