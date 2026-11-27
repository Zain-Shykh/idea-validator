import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentops.main import build_app
from agentops.repositories.index import IndexRepository
from agentops.settings import AgentOpsSettings
from common.contracts.ops import OpsSummary, RegistryResponse, Report

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_empty_reads_match_contracts(client: TestClient) -> None:
    assert client.get("/runs/run_1/trace").json() == []
    summary = OpsSummary.model_validate_json(client.get("/summary").text)
    assert (summary.runs_total, summary.cost_per_verdict_usd, summary.cache_hit_rate) == (0, 0, 0)
    for path in ("/drift/latest", "/eval/latest"):
        response = client.get(path)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")


def test_latest_report_of_each_kind(client: TestClient, settings: AgentOpsSettings) -> None:
    url = settings.db_url()
    assert url is not None
    index = IndexRepository(url)
    now = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
    # stored out of order: the newest wins, and each kind has its own
    for kind, sha, age in (("drift", "new", 0), ("drift", "old", 2), ("eval", "ev", 1)):
        index.add_report(
            Report(kind=kind, git_sha=sha, created_at=now - timedelta(days=age),
                   metrics={"n": age}, path=f"{sha}.json")
        )  # fmt: skip
    index.dispose()
    drift = Report.model_validate_json(client.get("/drift/latest").text)
    assert (drift.git_sha, drift.metrics, drift.created_at) == ("new", {"n": 0}, now)
    assert Report.model_validate_json(client.get("/eval/latest").text).path == "ev.json"


def test_registry_matches_lock(client: TestClient) -> None:
    response = RegistryResponse.model_validate_json(client.get("/registry").text)
    lock = json.loads((ROOT / "config/registry.lock.json").read_text())
    listed = {f"{e.kind}/{e.name}": (e.version, e.sha256) for e in response.entries}
    assert listed == {k: (v["version"], v["sha256"]) for k, v in lock.items()}


def test_invalid_registry_is_not_ready_but_still_traces(
    settings: AgentOpsSettings, tmp_path: Path
) -> None:
    broken = settings.model_copy(update={"prompts_dir": str(tmp_path / "nothing")})
    with TestClient(build_app(broken)) as client:
        assert client.get("/ready").status_code == 503
        response = client.get("/registry")
        assert (response.status_code, response.json()["error"]["code"]) == (503, "REGISTRY_INVALID")
        assert client.post("/trace", json=[]).status_code == 202


def test_without_a_database_nothing_is_ready(
    settings: AgentOpsSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    unset = settings.model_copy(update={"database_url": None, "postgres_host": None})
    with TestClient(build_app(unset)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.get("/summary").status_code == 503


def test_unmigrated_database_is_not_ready(settings: AgentOpsSettings, tmp_path: Path) -> None:
    empty = AgentOpsSettings(**settings.model_dump() | {"database_url": f"sqlite:///{tmp_path}/n"})
    with TestClient(build_app(empty)) as client:
        assert client.get("/ready").status_code == 503


def test_url_is_built_from_the_postgres_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTOPS_DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:/word")
    url = AgentOpsSettings().db_url()
    assert url is not None
    assert (url.host, url.database, url.password) == ("postgres", "ops", "p@ss:/word")
    assert "p@ss" not in str(url) and "p@ss" not in repr(AgentOpsSettings())
