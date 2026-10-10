from alembic import context

from adawork.db.models import Base


def include_object(obj, name, type_, reflected, compare_to):
    # FTS5 tables are created by hand in migrations; autogenerate must not touch them.
    return not (type_ == "table" and name.startswith("jobs_fts"))


connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=False,  # batch mode rebuilds tables, dropping the FTS5 triggers
    include_object=include_object,
)

with context.begin_transaction():
    context.run_migrations()
