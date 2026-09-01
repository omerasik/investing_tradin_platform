"""Alembic environment for the production PostgreSQL schema."""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

env_url = (
    os.environ.get("POSTGRES_TEST_DSN")
    or os.environ.get("POSTGRES_DSN")
    or os.environ.get("DATABASE_URL")
)
if env_url:
    if env_url.startswith("postgresql://"):
        env_url = "postgresql+psycopg://" + env_url[len("postgresql://") :]
    elif env_url.startswith("postgres://"):
        env_url = "postgresql+psycopg://" + env_url[len("postgres://") :]
    config.set_main_option("sqlalchemy.url", env_url)


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
