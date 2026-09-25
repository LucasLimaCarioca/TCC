# Dados históricos restritos

- O CSV original é `data/pedidos.csv`. Nunca versionar, publicar ou enviar dados
  históricos reais para serviços externos, inclusive após desidentificação.
- Toda saída derivada deve permanecer em `data/`: usar `data/processed/` para
  datasets, `data/reports/` para relatórios e `data/artifacts/` para artefatos.
  Cópias, amostras reais, gráficos e exportações também são dados restritos.
- Não inserir registros reais em código, documentação, testes ou logs. Testes
  devem usar apenas dados fictícios, independentes do CSV original.
- Manter chaves HMAC fora do repositório e nunca exibir seu conteúdo. Não confundir
  pseudonimização com autorização para publicar dados.
- Executar `python3 scripts/check_private_data.py` antes de concluir alterações.
  Não usar `git add -f` para dados nem desativar o hook de proteção.
- Na fase de preparação, validar o schema, remover informações pessoais e
  produzir apenas os atributos necessários à previsão, conforme o plano local
  `tcc_codex_docs/docs/TCCII_IMPLEMENTATION_PLAN.md`.
