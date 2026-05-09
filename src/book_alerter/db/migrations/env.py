from logging.config import fileConfig

from alembic import context
from sqlmodel import SQLModel

from book_alerter.db.session import get_database_url
from book_alerter.db import models  # noqa: F401  registers tables on SQLModel.metadata

config = context.config
fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", get_database_url())

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import engine_from_config, pool
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
