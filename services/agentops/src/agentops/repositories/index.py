"""``traces_index`` and ``eval_runs`` in the ``ops`` database (architecture §8.3, DATA-9)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from common.contracts.ops import Report

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


class EvalRun(Base):
    """Evaluation and drift reports (M13-4, M13-5)."""

    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_kind_created_at", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    git_sha: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(_JSON)
    path: Mapped[str | None]


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

    def add_report(self, report: Report) -> None:
        with Session(self.engine) as session, session.begin():
            session.add(EvalRun(**report.model_dump()))

    def latest_report(self, kind: str) -> Report | None:
        newest = (
            select(EvalRun)
            .where(EvalRun.kind == kind)
            .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
            .limit(1)
        )
        with Session(self.engine) as session:
            row = session.scalars(newest).first()
            if row is None:
                return None
            created = row.created_at
            return Report.model_validate(
                {
                    "kind": row.kind,
                    "git_sha": row.git_sha,
                    # SQLite forgets the zone; every stored time is UTC
                    "created_at": created if created.tzinfo else created.replace(tzinfo=UTC),
                    "metrics": row.metrics,
                    "path": row.path,
                }
            )

    def ping(self) -> bool:
        """Reachable and migrated."""
        with Session(self.engine) as session:
            session.execute(select(TraceIndex.run_id).limit(1))
            session.execute(select(EvalRun.id).limit(1))
        return True

    def dispose(self) -> None:
        self.engine.dispose()
