from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection, version_table="explorer_schema_version")
with context.begin_transaction():
    context.run_migrations()
