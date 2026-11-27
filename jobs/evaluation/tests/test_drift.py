"""``drift.py`` (spec Z10, A4 #11). Cards, niches and verdicts here are made up."""

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import drift
import reports
from agentops.main import build_app
from agentops.migrate import upgrade
from agentops.repositories.index import IndexRepository, TraceIndex
from agentops.settings import AgentOpsSettings
from common.contracts.kb import KbStats
from common.contracts.ops import Report
from common.http import ServiceClient

NOW = datetime.now(UTC)
STATS = KbStats(
    cards_by_source={"hackernews": 30, "github": 12},
    cards_by_niche={"education": 33, "health": 9},
    median_age_days=41.5,
    pct_fresh_90d_by_source={"hackernews": 0.8, "github": 0.5},
    last_ingestion_by_source={"github": NOW},
    kb_version="kb-7",
)


def row(n: int, days: float, verdict: str, user: str = "u_1") -> TraceIndex:
    when = NOW - timedelta(days=days)
    return TraceIndex(
        run_id=f"run_{n}", workflow="validate", user_id=user, status="COMPLETED", steps=1,
        tokens_in=0, tokens_out=0, cost_usd=0, latency_ms=0, cache_hits=0, fallbacks=0,
        started_at=when, finished_at=when, versions={}, metrics={"verdicts": [verdict]},
    )  # fmt: skip


def rows() -> list[TraceIndex]:
    recent = [row(n, 1, "NO-GO") for n in range(10)]
    before = [row(100 + n, 20, "GO") for n in range(10)]
    # outside both windows, and the harness's own runs
    other = [row(200, 60, "PIVOT"), row(201, 1, "PIVOT", user="u_eval_abc_lab_01")]
    return recent + before + other


def test_freshness_and_coverage() -> None:
    report = drift.build_report(STATS, [], None, now=NOW, sha="s", fake=False)
    found = report["metrics"]
    assert found["kb_version"] == "kb-7" and found["freshness"]["median_age_days"] == 41.5
    assert found["freshness"]["pct_fresh_90d_by_source"]["github"] == 0.5
    assert found["coverage"]["thin_niches"] == ["health"]
    assert (found["coverage"]["covered_share"], found["coverage"]["cards"]) == (0.5, 42)
    assert found["coverage"]["change"] is None and found["coverage"]["newly_thin"] == []
    previous = {"coverage": {"covered_share": 1.0, "thin_niches": []}}
    after = drift.coverage(STATS, previous)
    assert (after["change"], after["newly_thin"]) == (-0.5, ["health"])
    assert (
        drift.coverage(KbStats(cards_by_source={}, cards_by_niche={}), None)["covered_share"]
        is None
    )


def test_verdict_mix_windows_and_eval_users() -> None:
    found = drift.verdict_mix(rows(), NOW)
    assert (found["current"], found["baseline"]) == ({"NO-GO": 10}, {"GO": 10})
    # by hand: two labels swap 10.5/12 and 0.5/12, the other two are equal
    expected = 2 * (10 / 12) * math.log(21)
    assert math.isclose(found["psi"], expected) and found["note"] is None


def test_psi_is_zero_for_the_same_mix_and_absent_for_small_windows() -> None:
    assert drift.psi({"GO": 5, "PIVOT": 5}, {"GO": 5, "PIVOT": 5}) == 0
    found = drift.verdict_mix([row(1, 1, "GO"), row(2, 20, "GO")], NOW)
    assert found["psi"] is None and "not enough data" in found["note"]


def kb(handler: Any) -> ServiceClient:
    return ServiceClient("http://kb:8000", transport=httpx.MockTransport(handler), retries=0)


def stats_reply(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=STATS.model_dump_json())


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentOpsSettings:
    url = f"sqlite:///{tmp_path}/ops.db"
    upgrade(url)
    monkeypatch.setenv("AGENTOPS_DATABASE_URL", url)
    index = IndexRepository(url)
    for item in rows():
        index.upsert(item)
    index.dispose()
    root = reports.ROOT
    return AgentOpsSettings(
        database_url=url, traces_dir=str(tmp_path / "traces"),
        rubrics_dir=str(root / "rubrics"), prompts_dir=str(root / "prompts"),
        config_dir=str(root / "config"),
    )  # fmt: skip


def test_the_report_is_written_stored_and_served(
    database: AgentOpsSettings, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    assert drift.main(["--out", str(out)], kb(stats_reply)) == 0
    (path,) = out.glob("drift-*.json")
    written = json.loads(path.read_text())
    assert written["metrics"]["verdict_mix"]["psi"] > 0
    with TestClient(build_app(database)) as api:
        served = Report.model_validate_json(api.get("/drift/latest").text)
    assert served.metrics == written["metrics"] and served.path == path.name
    # the second report compares with the first
    assert drift.main(["--out", str(tmp_path / "again")], kb(stats_reply)) == 0
    (second,) = (tmp_path / "again").glob("drift-*.json")
    assert json.loads(second.read_text())["metrics"]["coverage"]["change"] == 0


def test_a_fake_report_is_not_stored_nor_put_in_results(
    database: AgentOpsSettings, tmp_path: Path
) -> None:
    assert drift.main(["--fake"], kb(stats_reply)) == 2  # the default folder is eval/results
    assert drift.main(["--fake", "--out", str(tmp_path / "f")], kb(stats_reply)) == 0
    assert list((tmp_path / "f").glob("FAKE-drift-*.json"))
    with TestClient(build_app(database)) as api:
        assert api.get("/drift/latest").status_code == 404
    with pytest.raises(reports.OutputError, match="not stored"):
        reports.store({"fake": True}, tmp_path / "x.json")


def test_without_the_knowledge_base_there_is_no_report(
    database: AgentOpsSettings, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    assert drift.main(["--out", str(out)], kb(lambda _: httpx.Response(503))) == 2
    assert not out.exists()


def test_without_the_database_there_is_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTOPS_DATABASE_URL", f"sqlite:///{tmp_path}/empty.db")
    assert drift.main(["--out", str(tmp_path / "out")], kb(stats_reply)) == 2
    monkeypatch.delenv("AGENTOPS_DATABASE_URL")
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("KB_URL", raising=False)
    assert drift.main(["--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()
