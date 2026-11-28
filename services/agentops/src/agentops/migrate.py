"""``python -m agentops.migrate``: create the ``ops`` database if needed, then upgrade it.
Run by the ``db-migrate`` init container, never at import time.
"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from agentops.settings import AgentOpsSettings


def ensure_database(url: URL) -> None:
    if not url.drivername.startswith("postgresql"):
        return
    server = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    try:
        with server.connect() as connection:
            query = text("SELECT 1 FROM pg_database WHERE datname = :name")
            if not connection.execute(query, {"name": url.database}).scalar():
                quoted = server.dialect.identifier_preparer.quote(url.database or "")
                connection.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        server.dispose()


def alembic_config(url: URL | str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["url"] = make_url(url)
    return config


def upgrade(url: URL | str, revision: str = "head") -> None:
    ensure_database(make_url(url))
    command.upgrade(alembic_config(url), revision)


def downgrade(url: URL | str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def main() -> int:
    url = AgentOpsSettings().db_url()
    if url is None:
        print("no database configured (POSTGRES_HOST or AGENTOPS_DATABASE_URL)", file=sys.stderr)
        return 1
    upgrade(url)
    print("database is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
