# Ajustes da nova rodada de validação manual

Esta rodada complementa as correções anteriores, preservando Flask,
SPADE/XMPP, os testes existentes e as previsões publicadas. Não há treinamento,
republicação de artefatos, limpeza de histórico nem criação automática de
produtos a partir do CSV.

## Quantidades na tela de Estoque

Os campos de movimentação e estoque mínimo reutilizam a lógica decimal dos
controles de matérias-primas. Cada clique aumenta ou diminui exatamente uma
unidade, conservando a parte fracionária. A redução respeita zero, inclusive
quando o valor anterior é inferior a uma unidade.

A entrada manual de matérias-primas aceita vírgula ou ponto, com até nove casas
decimais. A interface usa inteiros de precisão arbitrária para a aritmética
decimal, sem somas em ponto flutuante. O backend continua sendo a autoridade
de validação; produtos acabados permanecem restritos a unidades inteiras.
Uma saída inválida continua sendo rejeitada, com mensagem próxima ao formulário
e campos preservados.

## Valores no gráfico de previsão

Uma seleção diária de até sete períodos apresenta todas as datas e o valor
inteiro de cada ponto. Esses inteiros são os mesmos da tabela e continuam
seguindo HALF_UP: a fração 0,5 arredonda para cima. O total da tela é a soma das
linhas arredondadas, conforme documentado na rodada anterior.

Para intervalos maiores, todos os pontos da curva permanecem presentes. A
quantidade de rótulos numéricos acompanha a largura disponível para reduzir
sobreposição. Os valores individuais continuam acessíveis na interação e na
tabela. O tratamento considera empates, valores próximos de zero e telas
estreitas. Essas mudanças são de apresentação; previsões originais, métricas e
regras decimais de risco não são arredondadas.

## Histórico e catálogo operacional

`VendaHistorica` guarda observações agregadas por código textual. `Produto`
guarda nome, categoria, sabor, preço, saldo, mínimo e estado do catálogo
operacional. Estar presente no histórico não cria um produto operacional e não
define se ele ainda é comercializado.

A identidade histórica é `codigo_produto`, preservado integralmente. O ID
interno identifica a linha do catálogo e não é um código comercial. Não se
criam correspondências por igualdade numérica, remoção de zeros, nome ou
categoria.

A investigação dos vínculos existentes foi registrada exclusivamente em
`data/reports/`. O relatório privado relaciona os vínculos e os indícios que
exigem revisão humana. Seu conteúdo não é reproduzido nesta documentação ou
nos testes. Nenhum vínculo real foi modificado como consequência automática
da investigação.

Diferenças de categoria geram sinalização de possível inconsistência, sem
concluir que o vínculo está errado. Categoria também não substitui o nome
completo de um produto. O operador precisa conferir os dados comerciais.

## Fluxo administrativo explícito

A tela `/catalogacao` lista códigos do histórico importado, da publicação
disponível e dos vínculos existentes. A listagem distingue:

- código sem associação;
- associação pendente de revisão;
- possível inconsistência para conferência;
- associação revisada explicitamente pelo operador.

Um código sem associação permite escolher um produto operacional livre ou
cadastrar um novo produto. A criação exige nome, categoria, sabor, preço,
estoque inicial e estoque mínimo informados. Descrição é opcional. A ativação
exige escolha explícita; sem ela, o produto permanece inativo. Produtos
descontinuados, desconhecidos ou sem dados suficientes continuam pendentes.
Produtos marcados como demonstração não recebem associações operacionais.

Revisar, associar, alterar, desassociar ou reverter exige confirmação explícita,
identificação do operador e motivo. A identificação é declarada pelo operador;
este fluxo não acrescenta autenticação nem assinatura de identidade.

`GET /api/catalogacao` é somente leitura. As escritas passam por
`POST /api/catalogacao/operacoes`, com a ação escolhida, sua confirmação e as
referências de estado obtidas na consulta. A autoridade de Estoque executa a
mutação, mantendo o caminho SPADE/XMPP quando o runtime está ativo.

A CLI existente `previsao associar-produto` também passa a exigir `--confirmar`,
`--operador` e `--motivo`, registrando revisão e auditoria pelo mesmo serviço.
Ela não substitui a operação de transferência de um vínculo existente para
outro produto, que exige a conferência específica da tela de catalogação.

## Integridade, auditoria e reversão

O código textual continua único em `Produto.codigo_externo`. Um produto não
recebe um segundo código conflitante. O serviço revalida o estado consultado
dentro da transação e rejeita alterações concorrentes. A chave de operação
permite repetir o mesmo pedido de forma segura; reutilizar a chave com outro
conteúdo é conflito.

Criar um produto com saldo inicial positivo gera uma entrada auditável.
Cadastro, associação, revisão, saldo, movimentação, eventual alerta imediato e
auditoria são confirmados na mesma transação. Falha reverte o conjunto. Saldo
inicial zero não gera movimentação de quantidade zero.

A auditoria registra ação, operador, motivo e estados anterior e posterior.
Alterações de vínculos existentes não apagam vendas ou observações históricas.
A reversão exige nova confirmação e só acontece se o estado atual permitir
restaurar o vínculo sem conflito. Se uma operação posterior mudou o estado,
o serviço exige nova revisão em vez de sobrescrever silenciosamente.

**Reverter catalogação não estorna estoque.** A reversão restaura vínculo e
revisão. Um produto criado permanece cadastrado, com seu saldo e sua entrada
inicial auditável; não se apagam produtos, movimentos ou vendas. Ajustes de
estoque continuam no fluxo de movimentação, com seu próprio motivo e auditoria.

## Revisão dos vínculos e alertas preventivos

Associações legadas são preservadas e permanecem pendentes até revisão
explícita. A confirmação não é deduzida da existência do vínculo nem de uma
aprovação antiga de equivalência numérica. Divergência de metadados permanece
um indício a apresentar ao operador.

A revisão registra a identidade comercial e os metadados históricos
conferidos. Mudança nesses dados pode exigir nova revisão. Movimentações de
estoque e alterações do mínimo não representam mudança da identidade
comercial.

Uma associação pendente não habilita previsão operacional por produto nem
pode ser usada silenciosamente para criar ou resolver alertas preventivos.
Uma revisão que perdeu validade também exige conferência. Alertas anteriores
permanecem preservados, com sua avaliação pendente; ausência de aprovação não
equivale a demanda zero ou a estoque seguro.

A inspeção da previsão por código histórico continua disponível no painel,
assim como a visão geral. Não é necessário recalcular ou republicar modelos
para corrigir a catalogação. A persistência de alertas continua usando somente
`PREDICTIVE_ALERT_HORIZON_DAYS`; consultas personalizadas permanecem somente
leitura. As verificações do vínculo são repetidas na persistência para impedir
que uma resposta anterior à troca de associação seja confirmada como atual.

## Demonstrações isoladas

`python seed.py --demo` prepara exclusivamente
`data/artifacts/demonstracao.db`, com marcador privado e produtos identificados
por `demonstracao=True`. Reexecutar não redefine preço, saldo, mínimo, status
ou outros campos e não desativa produtos. O banco operacional padrão não é
aberto pelo seed. Consulte [DEMONSTRACAO.md](DEMONSTRACAO.md) para os comandos e
as condições de recusa.

O campo `demonstracao=False` em registros legados significa apenas que não há
marca explícita de demo. Ele não prova origem real. Não são excluídos nem
reclassificados automaticamente produtos por nome ou ID. A interface distingue
o ambiente demo e a identificação de seus produtos; a origem de legados exige
revisão humana.

## Migração e dados preservados

A revisão incremental `0009_catalogacao_historica` acrescenta a identificação
de demonstração, a revisão das associações e o registro de operações. Revisões
anteriores permanecem inalteradas. A atualização deve usar o comando existente,
que prepara backup privado antes de migrar um banco já utilizado:

```bash
.venv/bin/python create_db.py
```

A migration não corrige associações, preenche nomes, ativa produtos ou cria
produtos operacionais a partir do histórico. Não há alteração dos arquivos de
previsão já publicados. Os relatórios de investigação permanecem privados em
`data/reports/`, o banco em `data/artifacts/` e os testes usam somente dados
fictícios.

## Validação e pendências humanas

As regressões desta rodada cobrem controles de quantidade, rótulos do gráfico,
catalogação, conflitos, zeros à esquerda, preservação do histórico, auditoria,
saldo inicial, rollback, revisão de vínculos e isolamento da demonstração.
A validação final inclui as suítes Python e JavaScript, integração SPADE/XMPP,
dependências, sintaxe, privacidade e `git diff --check`.

Validação local concluída em 30/10/2026:

- `python -m pytest -q`: **1.294 testes aprovados**, incluindo as novas
  integrações HTTP → Estoque → persistência e as corridas de revisão durante RPC.
- Seleção de regressão com `--spade`: **61 testes aprovados**.
- `node --test tests/js/*.test.cjs`: **63 testes aprovados**.
- `pip check`, sintaxe dos quatro scripts JavaScript, proteção de dados no
  índice Git e `git diff --check`: aprovados.

As suítes Python emitiram avisos de depreciação de dependências: 183 na suíte
completa e 103 na seleção SPADE. Não houve falhas. Estes são resultados locais
dos comandos do CI, não uma execução remota do GitHub Actions.

A migração `0009` foi aplicada ao banco local com backup privado. A conferência
por hashes de todas as colunas anteriores confirmou a preservação dos registros,
incluindo vínculos, saldos, preços, histórico e publicações. A migração não
aprovou qualquer associação. O comprovante e o relatório de investigação ficam
exclusivamente em `data/reports/`.

Os testes de largura simulada e interação do DOM passaram. A conferência visual
em desktop e celular permanece pendente: a ferramenta de automação não abriu
uma sessão de navegador nesta execução. Nenhum dado histórico real foi usado
nos testes automatizados.

O operador ainda precisa confirmar os vínculos existentes e os indícios do
relatório privado. Novos cadastros dependem de dados comerciais que o histórico
agregado não fornece: nome completo, categoria/sabor corretos, preço vigente,
saldo inicial, mínimo e situação de atividade. Esses valores não são inventados.
Origem de produtos legados e eventual retirada de dados fictícios também
dependem de decisão humana. Nenhum vínculo real é trocado ou removido sem essa
confirmação e nenhuma exclusão foi automatizada.
