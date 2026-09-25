# Fase 0 — Baseline do TCC I

Validação em Python 3.12.3 / Linux, branch `tcc-ii/fase-0-baseline`.

## Escopo realizado

- `create_app(test_config=None)` aceita configuração antes de inicializar o
  SQLAlchemy. Sem argumentos, mantém o banco e o comportamento anteriores.
- Dependências diretas fixadas; pytest separado em `requirements-dev.txt`.
  Flask, Flask-SQLAlchemy, SQLAlchemy e SPADE mantêm as versões do ambiente.
  Streamlit 1.64.0 é compatível com o requisito `packaging==26.2` do SPADE 4.1.4.
- SQLite temporário por teste, com catálogo fictício equivalente ao seed.
  Os testes não importam `seed.py` nem `create_db.py` e não usam o banco local.
- README corrigido para cinco clientes, 11 produtos e o efeito real do seed:
  ele redefine saldos/preços e desativa produtos fora do catálogo inicial.
- Nenhuma regra de atendimento ou vendas foi refatorada; nenhum modelo novo,
  agente SPADE, previsão, LLM ou receita/BOM foi implementado.

## Cobertura

| Arquivo | Casos | O que verifica |
|---|---:|---|
| `tests/test_regressao_tcc_i.py` | 8 | Os oito cenários obrigatórios, respostas, contexto, vendas e estoque |
| `tests/test_venda_service.py` | 13 | Venda simples/múltipla, totais, cliente, estoque, rejeições e ausência de venda parcial |
| `tests/test_atendimento_contexto.py` | 4 | Cancelamento, confirmação repetida, isolamento por cliente e revalidação do saldo |
| `tests/test_http.py` | 9 | Três telas, redirecionamento, APIs, produtos inativos e entradas inválidas cobertas |
| `tests/test_protecao_dados.py` | 25 | Caminhos restritos, arquivos permitidos, ignore e bloqueio efetivo de commit forçado em repositório temporário |

Os oito cenários exigidos são consulta de produtos, disponibilidade específica,
pedido múltiplo confirmado, categoria compartilhada com vários sabores, produto
sem estoque, quantidade acima do saldo, preço específico e preços múltiplos.

## Reprodução

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
python -m pytest -q --junitxml=/tmp/tcc-fase-0.xml
```

Para executar a aplicação já inicializada:

```bash
python run.py
```

Em uma instalação nova, executar `python create_db.py` e `python seed.py` antes
inicializa as tabelas e os dados simulados. Não repetir o seed para preservar
saldos de uma demonstração já utilizada.

A validação inicial do baseline teve **34 testes aprovados** e 39 avisos de
obsolescência de `datetime.utcnow()` nos modelos existentes. Os avisos não foram
ocultados nem corrigidos, pois a política de timestamps deve ser tratada à parte.
A proteção solicitada posteriormente acrescentou 25 casos à suíte. Validação
final em 25/09/2026: **59 passed, 39 warnings in 1.56s**; `pip check` retornou
`No broken requirements found.` Evidência JUnit: `/tmp/tcc-fase-0.xml`.

## Comportamentos que precisam de decisão antes da Fase 1

As observações abaixo foram reproduzidas com dados fictícios em SQLite
temporário. Permanecem no baseline para evitar mudanças de regra nesta fase.

1. **Produto repetido no serviço:** dois itens do mesmo produto com quantidade
   12 cada, diante de saldo 20, passam na validação individual e deixam saldo
   -4. Decidir se o contrato deve agregar ou rejeitar itens duplicados.
2. **Pedido vazio:** `registrar_vendas_multiplas([])` informa sucesso e retorna
   lista vazia. Decidir se pedidos sem itens devem falhar explicitamente.
3. **Confirmação sem saldo:** se o saldo diminui entre pedido e confirmação, a
   venda é rejeitada, mas o contexto é apagado. Decidir entre manter o pedido
   para correção ou exigir um novo pedido. Esse comportamento tem teste.
4. **Quantidade zero no diálogo:** o agente pede confirmação de zero unidades;
   somente o serviço rejeita ao confirmar. Decidir se a rejeição deve ocorrer
   na interpretação inicial.
5. **Palavra “sabor” em pedido:** “quero 2 caixas de 10L sabor chocolate” retorna
   catálogo, porque a consulta de sabores tem prioridade. Decidir quais formas
   de linguagem serão aceitas na futura refatoração.

A aprovação dos testes demonstra os cenários cobertos, não ausência de todos os
problemas de entrada, concorrência ou persistência.

## Proteção dos dados adicionada à retomada

O original `data/pedidos.csv` foi localizado sem leitura ou divulgação de seus
registros. A consulta ao índice Git e ao histórico local para `data/` e formatos
comuns de datasets/bancos não encontrou arquivos de dados versionados.

`.gitignore` exclui todos os arquivos em `data/`, inclusive saídas em JSON, HTML
ou imagens. Derivados devem ficar em `data/processed/`, `data/reports/` e
`data/artifacts/`; formatos comuns também são ignorados fora dessas pastas.
`AGENTS.md` registra essa regra para o desenvolvimento futuro.

O hook `.githooks/pre-commit` executa `scripts/check_private_data.py`, que bloqueia
caminhos restritos no índice mesmo após inclusão forçada. Não examina conteúdos
nem substitui a obrigação de não copiar registros reais para código/documentos.
Ativação em cada clone:

```bash
chmod +x .githooks/pre-commit
git config --local core.hooksPath .githooks
python3 scripts/check_private_data.py
```

A fase de preparação e desidentificação precede a Fase 1 no plano atualizado.
O pipeline ainda não foi executado ou implementado; todas as suas saídas futuras,
mesmo desidentificadas, permanecem excluídas da publicação conforme solicitado.

## Arquivos da execução

Alterados no baseline: `README.md`, `requirements.txt`, `app/app.py`.
Criados no baseline: `requirements-dev.txt`, `pytest.ini`, `tests/conftest.py`,
`tests/test_regressao_tcc_i.py`, `tests/test_venda_service.py`,
`tests/test_atendimento_contexto.py`, `tests/test_http.py` e este relatório.

Acréscimos de proteção: `.gitignore`, `AGENTS.md`, `.githooks/pre-commit`,
`scripts/check_private_data.py`, `tests/test_protecao_dados.py` e documentação
no README. As exclusões preexistentes do usuário foram preservadas.
