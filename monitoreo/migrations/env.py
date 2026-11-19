"""Entorno de Alembic. La URL la decide monitoreo.migrate (nunca alembic.ini)."""
from alembic import context

from monitoreo.db import Base
from monitoreo import models  # noqa: F401  registra las tablas

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        raise RuntimeError("Usa monitoreo.migrate para ejecutar migraciones")
    context.configure(connection=connectable, target_metadata=target_metadata,
                      render_as_batch=True,  # SQLite no soporta ALTER completo: Alembic recrea la tabla
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Modo offline no soportado")
run_migrations_online()
