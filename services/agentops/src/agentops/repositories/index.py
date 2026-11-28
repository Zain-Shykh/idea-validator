"""``traces_index`` in the ``ops`` database (architecture §8.3, DATA-9)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, String, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

_JSON = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TraceIndex(Base):
    __tablename__ = "traces_index"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workflow: Mapped[str | None]
    user_id: Mapped[str | None]
    status: Mapped[str]
    steps: Mapped[int]
    tokens_in: Mapped[int] = mapped_column(BigInteger)
    tokens_out: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[float]
    latency_ms: Mapped[int] = mapped_column(BigInteger)
    cache_hits: Mapped[int]
    fallbacks: Mapped[int]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    versions: Mapped[dict[str, Any]] = mapped_column(_JSON)
    # what /summary needs beyond the columns, so it never reads trace files
    metrics: Mapped[dict[str, Any]] = mapped_column(_JSON)


class IndexRepository:
    def __init__(self, url: URL | str) -> None:
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if str(url).startswith("postgresql"):
            # a slow database must not hold requests (every call has a timeout)
            kwargs["connect_args"] = {"connect_timeout": 2, "options": "-c statement_timeout=5000"}
            kwargs["pool_timeout"] = 5
        self.engine = create_engine(url, **kwargs)

    def upsert(self, row: TraceIndex) -> None:
        with Session(self.engine) as session, session.begin():
            session.merge(row)

    def all(self) -> Sequence[TraceIndex]:
        # ponytail: loads every run; aggregate in SQL once there are many thousands
        with Session(self.engine) as session:
            return session.scalars(select(TraceIndex)).all()

    def get(self, run_id: str) -> TraceIndex | None:
        with Session(self.engine) as session:
            return session.get(TraceIndex, run_id)

    def ping(self) -> bool:
        """Reachable and migrated."""
        with Session(self.engine) as session:
            session.execute(select(TraceIndex.run_id).limit(1))
        return True

    def dispose(self) -> None:
        self.engine.dispose()
