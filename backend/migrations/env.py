"""Alembic environment for Ratiocination (SPEC §2.1, §4).

* ``target_metadata`` is ``radar.models.Base.metadata``.
* ``render_as_batch=True`` on SQLite (ALTERs become table rebuilds).
* The URL comes from ``sqlalchemy.url`` when set, else ``radar.config.get_settings().database_url``.
* ``radar.db.init_db()`` passes an open connection in ``config.attributes["connection"]``.
* ``radar.models.UTCDateTime`` renders as ``sa.DateTime(timezone=True)`` and JSON columns as
  ``sa.JSON()``, so migration files never import application code.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import JSON, create_engine, pool
from sqlalchemy.engine import Connection

from radar.config import get_settings
from radar.models import Base, UTCDateTime

config = context.config

if config.config_file_name is not None and not config.attributes.get("connection"):
    # Only when Alembic runs from the command line with alembic.ini; never reconfigure the app's logging.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

log = logging.getLogger("alembic.env")
target_metadata = Base.metadata


def _url() -> str:
    url = (config.get_main_option("sqlalchemy.url") or "").strip()
    return url or get_settings().database_url


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render application column types as plain SQLAlchemy types."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    if type_ == "type" and isinstance(obj, JSON):
        return "sa.JSON()"
    return False


def _configure_kwargs(connection: Connection | None = None, url: str | None = None) -> dict[str, Any]:
    dialect_name = connection.dialect.name if connection is not None else (url or "").split(":", 1)[0]
    is_sqlite = dialect_name.startswith("sqlite")
    return {
        "target_metadata": target_metadata,
        "render_as_batch": is_sqlite,
        "compare_type": True,
        "render_item": render_item,
    }


def run_migrations_offline() -> None:
    url = _url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_configure_kwargs(url=url),
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, **_configure_kwargs(connection=connection))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with_connection(connection)
        return
    url = _url()
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)
    try:
        with engine.connect() as conn:
            _run_with_connection(conn)
            conn.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
