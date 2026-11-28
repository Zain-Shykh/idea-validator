"""Alembic environment; the database URL comes from ``agentops.migrate``."""

from alembic import context
from sqlalchemy import create_engine

from agentops.repositories.index import Base

engine = create_engine(context.config.attributes["url"])
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
