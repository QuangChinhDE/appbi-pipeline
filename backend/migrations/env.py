"""Alembic environment.

The URL and the metadata both come from the application, so a migration cannot
be run against a database the code does not know about, and autogenerate always
compares against the models that will actually be loaded.

Migrations run synchronously on purpose: Alembic's own tooling is synchronous,
and a migration is a one-shot operation with no concurrency to gain from.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base

# Importing the package registers every table on Base.metadata; without it
# autogenerate would think the schema should be empty.
import app.models  # noqa: F401

# Transform's tables live in their own package; autogenerate needs them too.
app.models.register_transform_tables()

config = context.config
# Only take over logging when nobody else has set it up.
#
# `alembic.ini` describes logging for the standalone CLI: root at WARNING with a
# plain stderr handler. Applying that inside a host process replaces whatever
# the host installed. `python -m app.bootstrap` migrates and *then* seeds, so
# the deploy container printed its migration lines, seeded the catalogue,
# republished manifests and exited 0 having said nothing about any of it --
# root had been reset to WARNING and the JSON handler dropped. A bootstrap that
# works silently is indistinguishable from one that skipped, and a warning
# raised in that window is simply lost.
#
# `disable_existing_loggers=False` is not enough on its own: the damage is the
# root logger's level and handlers being replaced, not existing loggers being
# switched off.
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """The application URL, with the async driver swapped out.

    The app talks asyncpg; Alembic talks psycopg 3. The driver is named
    explicitly because a bare `postgresql://` makes SQLAlchemy reach for
    psycopg2, which is not installed and never will be.
    """
    return settings.database_url.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Type changes are a common source of silent drift, so autogenerate
            # is told to look for them.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
