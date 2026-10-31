# Consolidação de exportações históricas

Quando duas exportações são continuação uma da outra, prepare cada fonte com
`scripts.prepare_history`. Depois consolide os relatórios aprovados, informando
explicitamente qual fonte prevalece nos dias compartilhados:

```bash
python -m scripts.consolidate_history \
  --base data/reports/preparacao-ANTERIOR.json \
  --priority data/reports/preparacao-CONTINUACAO.json
```

Os identificadores acima são exemplos; use os caminhos fornecidos nas suas
execuções. As duas preparações devem ter a mesma versão e usar `dia_fonte`.

A prioridade é por **dia inteiro**, não por produto ou pedido: todos os registros
da base em um dia presente na continuação são substituídos pelos registros
desse dia na continuação. Um produto ausente na continuação não é recuperado da
base nesse dia. As quantidades não são somadas entre as duas exportações.
Essa política exige que a fonte prioritária represente o dia que deve prevalecer.

O comando confere os hashes, caminhos privados, schema, observações repetidas e
metadados comerciais, inclusive entre fontes. Inconsistências bloqueiam a saída.
Ele não aceita automaticamente versões diferentes nem altera nomes para
resolver conflitos. Os originais, datasets anteriores e banco são preservados.

Cada execução cria:

- `data/processed/consolidacao-<id>/demanda_diaria.csv`;
- `data/reports/consolidacao-<id>.json`.

O relatório registra as duas origens e seus hashes, a regra aplicada e os dias
substituídos. `sha256_fonte` identifica o manifesto da composição, não os bytes
de um CSV bruto. O hash do dataset identifica os bytes do CSV unificado.
Reexecuções idênticas produzem o mesmo conteúdo, em caminhos novos.

A aprovação técnica da consolidação **não aprova a revisão para previsão**.
Antes do treinamento, confirme a cobertura completa e o último dia completo,
revise calendário, lacunas e metadados, e produza o recorte e a revisão vinculados
ao novo dataset. Não reutilize a revisão antiga para representar dados novos,
nem altere seus hashes manualmente. Veja [a preparação](PREPARACAO_DADOS.md)
e [a revisão antes do treinamento](FASE_5_IMPORTACAO.md#revisão-privada-antes-do-treinamento).

Depois dessas confirmações, gere a revisão com o último dia completo e o
tratamento autorizado dos fins de semana. Exemplo com uma data fictícia:

```bash
python -m scripts.review_history \
  --preparation data/reports/consolidacao-IDENTIFICADOR.json \
  --cutoff 2025-01-31 --confirm-complete --exclude-weekends
```

Use `--keep-weekends` em vez de `--exclude-weekends` somente quando essa for a
decisão sobre o recorte. Isso não altera o calendário do motor: aberturas
excepcionais também precisam estar definidas no calendário operacional.
O comando não confirma por conta própria a integridade comercial de uma
exportação: `--confirm-complete` registra a declaração do operador e deve ser
usado apenas depois de conferir a fonte. Não apaga extremos nem modifica o
canônico. O recorte remove datas posteriores ao corte e, quando solicitado,
sábados e domingos. O motor aplica os demais fechamentos do calendário.

O terminal informa o caminho real de `data/reports/revisao-decisoes-<id>.json`.
Use esse caminho em `--review` no treinamento, junto com o snapshot da seleção
experimental exportado separadamente. Os identificadores da consolidação,
revisão, seleção e previsão são independentes. Veja [a seleção
experimental](SELECAO_EXPERIMENTAL.md) e [o treinamento](FASE_6_PREVISAO.md).

Não importe automaticamente a consolidação sobre o banco existente: ela pode
conter correções para produtos/dias já persistidos, que exigem conciliação
explícita. A consolidação não altera a política de bloqueio do importador.

Todos os arquivos derivados permanecem restritos a `data/`, inclusive os
relatórios. O console informa apenas status e caminhos. A consolidação não
inicia o servidor web, treina modelos, preenche lacunas com zero ou publica
previsões.

Validação com dados fictícios:

```bash
python -m pytest tests/test_consolidacao_historico.py -q
python3 scripts/check_private_data.py
```
