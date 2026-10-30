# Ambiente de demonstração separado

`seed.py` prepara exclusivamente dados fictícios em um banco dedicado. A
importação do módulo não abre banco e executar sem `--demo` encerra antes de
criar a aplicação.

```bash
.venv/bin/python seed.py --demo
```

O destino é sempre `data/artifacts/demonstracao.db`, separado de
`data/artifacts/sorvetes.db`. Um marcador privado, `demonstracao.json` na mesma
pasta, identifica o ambiente criado pelo fluxo. Banco preexistente sem marcador,
marcador inválido e arquivos vinculados são recusados; não há adoção automática
de bancos existentes. Esses arquivos ficam em `data/`, ignorados pelo Git.

Os cadastros criados recebem `Produto.demonstracao=True`. O saldo inicial
positivo gera entrada auditável na mesma transação dos produtos e clientes
fictícios. Repetir o seed é aditivo: não redefine preço, saldo, mínimo,
descrição, status ou telefone de registros existentes e não desativa produtos.
Falha de persistência reverte a transação. Artefatos de uma tentativa nova
que falhou são removidos; bancos preexistentes não são excluídos.

Se houver produtos sem a marca explícita de demonstração, códigos externos
associados, vendas, histórico importado ou previsões no destino, a semeadura
recusa a execução, preservando o banco para revisão. Não copie dados reais para
esse ambiente. Ele existe para demonstrações e não é um processo de limpeza
do banco operacional.

Para abrir o ambiente já preparado pelo Flask CLI, em porta separada:

```bash
.venv/bin/python -m flask --app 'seed:create_demo_app' run --port 5001
```

Esse factory fornece `TCC_DEMO_MODE=True` para identificação da interface e
não semeia implicitamente. Como o Flask CLI normal, esse comando não inicia o
runtime SPADE/XMPP. O launcher multiagente operacional continua separado.
Manifestos e revisões privados configurados no shell operacional não são
herdados pela aplicação de demonstração.

Produtos legados não são classificados por nome, ID, saldo ou semelhança com
os exemplos. A migration mantém `demonstracao=False` nos legados, o que significa
apenas ausência de identificação explícita de demo. A origem desses registros
exige revisão humana. Nenhum produto fictício ou histórico é excluído.
