from alembic import context

# O runner fornece a conexão transacional; não carrega credenciais nem abre
# outra conexão que poderia migrar um banco diferente do selecionado.
connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Execute as migrações pelo comando Flask db upgrade ou create_db.py.")
context.configure(connection=connection, transactional_ddl=True, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
