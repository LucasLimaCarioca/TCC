# Migrações SQLite

Execute `python create_db.py` ou `python -m flask --app run.py db upgrade`.
Consulte `python -m flask --app run.py db current` para a revisão atual.

O runner em `app/migrations.py` fornece a conexão, backup privado e transação.
Não usar `db.create_all()` para atualizar instalações existentes nem `stamp`
para pular revisões. Não alterar revisões já aplicadas ao acrescentar fases:
criar uma nova revisão com `down_revision` apontando para a anterior.

Detalhes e recuperação: `docs/FASE_1_MODELOS_MIGRACOES.md`.
