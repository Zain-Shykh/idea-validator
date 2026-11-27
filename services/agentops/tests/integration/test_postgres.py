"""Needs Postgres: ``docker compose up -d postgres`` and AGENTOPS_TEST_DATABASE_URL naming a
**throwaway** database (it is created if missing and dropped afterwards).
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from trace_factories import seeded_runs, thousand_records, wire

from agentops.migrate import downgrade, upgrade
from agentops.repositories.files import TraceFiles
from agentops.repositories.index import IndexRepository
from agentops.services.aggregate import summarise
from agentops.services.ingest import TraceService, parse_batch
from common.contracts.core import TraceRecord

pytestmark = pytest.mark.integration


def _drop(url: URL) -> None:
    server = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    server.dispose()


@pytest.fixture
def url() -> Iterator[URL]:
    raw = os.environ.get("AGENTOPS_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("AGENTOPS_TEST_DATABASE_URL is not set")
    url = make_url(raw)
    # the fixture drops this database, so refuse anything that is not clearly a test one
    assert url.database and url.database.endswith("_test"), "database name must end in _test"
    _drop(url)
    yield url
    _drop(url)


def tables(url: URL) -> set[str]:
    engine = create_engine(url)
    names = set(inspect(engine).get_table_names())
    engine.dispose()
    return names


def test_migration_up_down(url: URL) -> None:
    upgrade(url)  # also creates the database
    assert "traces_index" in tables(url)
    upgrade(url)  # idempotent: the init container runs on every pod start
    downgrade(url)
    assert "traces_index" not in tables(url)
    upgrade(url)
    assert "traces_index" in tables(url)


def test_model_matches_the_migration(url: URL) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from agentops.repositories.index import Base

    upgrade(url)
    engine = create_engine(url)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()


def test_ingest_and_summary_on_postgres(url: URL, tmp_path: Path) -> None:
    upgrade(url)
    index = IndexRepository(url)
    service = TraceService(TraceFiles(tmp_path), index)
    for records in (thousand_records(), seeded_runs()):
        for start in range(0, len(records), 100):
            batch: list[TraceRecord] = parse_batch(
                json.dumps(wire(records[start : start + 100])).encode()
            )
            service.ingest(batch)
    service.ingest(parse_batch(json.dumps(wire(seeded_runs())).encode()))
    rows = {row.run_id: row for row in index.all()}
    assert len(rows) == 12
    assert (rows["run_3"].steps, rows["run_3"].tokens_in, rows["run_3"].fallbacks) == (100, 1000, 2)
    assert rows["run_3"].cost_usd == pytest.approx(0.0098)
    assert rows["run_3"].cache_hits == 24
    assert rows["run_a"].versions["rubrics"] == "r1"
    summary = summarise(list(rows.values()), rows["run_a"].finished_at)
    assert summary.verdicts_total == 3 and summary.blocked_injections == 4
    assert index.ping()
    index.dispose()
