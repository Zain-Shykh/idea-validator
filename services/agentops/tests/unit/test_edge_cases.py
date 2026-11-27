"""Spec Z3 A5/A7: limits, failures, retries, concurrency and hostile input."""

import errno
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from trace_factories import VERSIONS, event, record, seeded_runs, wire

from agentops import migrate
from agentops.main import build_app
from agentops.services.ingest import MAX_BYTES
from agentops.settings import AgentOpsSettings
from common.contracts.core import ErrorEnvelope, TraceRecord
from common.contracts.ops import OpsEvent, OpsSummary

ROOT = Path(__file__).resolve().parents[4]


def metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def send(client: TestClient, records: list[TraceRecord]) -> None:
    response = client.post("/trace", json=wire(records))
    assert response.status_code == 202, response.text


def summary(client: TestClient) -> OpsSummary:
    return OpsSummary.model_validate(client.get("/summary").json())


def lines(settings: AgentOpsSettings) -> int:
    return sum(
        len(p.read_bytes().splitlines()) for p in Path(settings.traces_dir).glob("*/*.jsonl")
    )


def test_exactly_1000_records_are_accepted(client: TestClient) -> None:
    send(client, [record(step_id=i) for i in range(1000)])
    assert len(client.get("/runs/run_1/trace").json()) == 1000


def test_byte_limit_is_inclusive_and_413_uses_the_envelope(client: TestClient) -> None:
    at_limit = b"[" + b" " * (MAX_BYTES - 2) + b"]"
    assert client.post("/trace", content=at_limit).status_code == 202
    response = client.post("/trace", content=at_limit + b" ")
    assert response.status_code == 413
    assert ErrorEnvelope.model_validate(response.json()).error.code == "PAYLOAD_TOO_LARGE"


def test_a_chunked_oversized_body_is_cut_off(client: TestClient) -> None:
    response = client.post("/trace", content=(b"x" * 1_000_000 for _ in range(6)))
    assert response.status_code == 413


def test_boundary_values_are_accepted(client: TestClient) -> None:
    run_id = "A.b_c:-" + "x" * 121
    edge = record(
        run_id,
        tokens_in=10**12,
        tokens_out=10**12,
        latency_ms=10**12,
        cost_usd=1e12,
        ts="2099-12-31T23:59:59Z",
    ).model_copy(update={"step_id": 2**53})  # the factory derives ts from the step id
    send(client, [edge, record(run_id, 0, ts="2000-01-01T00:00:00Z")])
    assert len(client.get(f"/runs/{run_id}/trace").json()) == 2
    assert summary(client).runs_total == 1


@pytest.mark.parametrize(
    "change",
    [
        {"tokens_out": 10**12 + 1},
        {"latency_ms": 10**12 + 1},
        {"cost_usd": 1e12 + 1},
        {"cost_usd": -0.1},
        {"cost_usd": "inf"},
        {"step_id": 2**53 + 1},
        {"step_id": -1},
        {"ts": "1999-12-31T23:59:59Z"},
        {"ts": "2100-01-01T00:00:00Z"},
        {"run_id": ""},
        {"agent": "   "},
    ],
)
def test_values_just_outside_the_limits_are_rejected(
    client: TestClient, settings: AgentOpsSettings, change: dict[str, object]
) -> None:
    response = client.post("/trace", json=[wire([record()])[0] | change])
    assert response.status_code == 422, response.text
    assert not Path(settings.traces_dir).exists()


@pytest.mark.parametrize("body", [b"[" * 100_000, b"[\xff]", b'[{"\\ud800": 1}]', b"", b"null"])
def test_hostile_bodies_get_422_not_500(client: TestClient, body: bytes) -> None:
    response = client.post("/trace", content=body)
    assert response.status_code == 422, response.text
    ErrorEnvelope.model_validate(response.json())


def test_odd_unicode_survives_the_round_trip(client: TestClient) -> None:
    text = "a\u2028b\u2029c\x85d\r\ne\x00f \U0001f600 \u202e \u0627\u0631"
    original = record(input=text, output=text, error=text, agent="agent \u00e9 ")
    send(client, [original])
    send(client, [record(step_id=2)])
    stored = [TraceRecord.model_validate(r) for r in client.get("/runs/run_1/trace").json()]
    assert stored[0] == original.model_copy(update={"agent": original.agent.strip()})
    assert len(stored) == 2


def test_a_lone_surrogate_inside_an_event_payload_cannot_wedge_ingest(client: TestClient) -> None:
    payload = '{"workflow": "validate", "user_id": "\\ud800"}'
    send(client, [record(tool="run_started", output=payload)])
    assert summary(client).runs_total == 1


def test_corrupt_stored_lines_are_skipped(client: TestClient, settings: AgentOpsSettings) -> None:
    send(client, [record(step_id=1)])
    path = Path(settings.traces_dir) / "2026-11-27/run_1.jsonl"
    path.write_bytes(b"\xff\xfe\n{}\n\n[1]\n" + path.read_bytes())
    send(client, [record(step_id=2)])
    assert [r["step_id"] for r in client.get("/runs/run_1/trace").json()] == [1, 2]


def test_same_batch_twice_changes_nothing(client: TestClient, settings: AgentOpsSettings) -> None:
    send(client, seeded_runs())
    first = summary(client)
    traces = client.get("/runs/run_a/trace").json()
    stored = lines(settings)
    send(client, seeded_runs())
    assert summary(client).model_dump(exclude={"generated_at"}) == first.model_dump(
        exclude={"generated_at"}
    )
    assert client.get("/runs/run_a/trace").json() == traces
    assert lines(settings) == stored


def test_concurrent_deliveries_of_the_same_batches_stay_exact(
    client: TestClient, settings: AgentOpsSettings
) -> None:
    batches = [
        [
            record("run_c", step, kind="llm_call", tokens_in=1, cost_usd=0.001)
            for step in range(start, start + 10)
        ]
        for start in range(0, 100, 10)
    ]
    service = client.app.state.traces  # type: ignore[attr-defined]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(service.ingest, batches * 4))
    row = service.index.get("run_c")
    assert (row.steps, row.tokens_in) == (100, 100)
    assert row.cost_usd == pytest.approx(0.1)
    assert lines(settings) == 100


def test_batch_that_fails_half_way_is_completed_by_the_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = client.app.state.traces  # type: ignore[attr-defined]
    batch = [
        event(run, 1, OpsEvent.INJECTION_BLOCKED, stage="m5", count=1)
        for run in ("run_x", "run_y", "run_z")
    ]
    before = metric("ideaval_injections_blocked_total", stage="m5")
    real = service.index.upsert

    def fail_second(row: object) -> None:
        if row.run_id == "run_y":  # type: ignore[attr-defined]
            raise OperationalError("insert", {}, Exception("down"))
        real(row)

    with monkeypatch.context() as patch:
        patch.setattr(service.index, "upsert", fail_second)
        response = client.post("/trace", json=wire(batch))
    assert response.status_code == 503
    assert ErrorEnvelope.model_validate(response.json()).error.code == "STORAGE_UNAVAILABLE"
    send(client, batch)
    assert (summary(client).runs_total, summary(client).blocked_injections) == (3, 3)
    assert metric("ideaval_injections_blocked_total", stage="m5") == before + 3


def test_disk_full_is_503_and_the_retry_stores_the_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = client.app.state.traces.files  # type: ignore[attr-defined]

    def full(*_: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(files, "append", full)
        assert client.post("/trace", json=wire([record()])).status_code == 503
    assert summary(client).runs_total == 0
    send(client, [record()])
    assert summary(client).runs_total == 1


def test_unreachable_postgres_fails_fast_and_keeps_the_lines(
    settings: AgentOpsSettings,
) -> None:
    dead = AgentOpsSettings(
        **settings.model_dump() | {"database_url": "postgresql+psycopg://u:hunter2@127.0.0.1:1/ops"}
    )
    started = time.monotonic()
    with TestClient(build_app(dead)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        for response in (client.post("/trace", json=wire([record()])), client.get("/summary")):
            assert response.status_code == 503 and "hunter2" not in response.text
        assert len(client.get("/runs/run_1/trace").json()) == 1
    assert time.monotonic() - started < 15


def test_latest_event_by_time_wins_even_when_it_arrives_first(client: TestClient) -> None:
    send(
        client,
        [
            event("run_o", 5, OpsEvent.RUN_STATUS, status="COMPLETED"),
            event("run_o", 4, OpsEvent.APPROVAL, approval_id="ap", decision="approved"),
        ],
    )
    send(
        client,
        [
            event("run_o", 2, OpsEvent.RUN_STATUS, status="RUNNING"),
            event("run_o", 1, OpsEvent.APPROVAL, approval_id="ap", decision="pending"),
        ],
    )
    result = summary(client)
    assert result.runs_by_status == {"COMPLETED": 1}
    assert result.approvals == {"pending": 0, "approved": 1, "declined": 0}


def test_only_decision_records_with_known_tools_are_events(client: TestClient) -> None:
    bad = metric("ideaval_trace_bad_events_total")
    send(
        client,
        [
            record(step_id=1, tool="something_else", output="free text"),
            record(step_id=2, kind="tool_call", tool="run_status", output='{"status":"FAILED"}'),
        ],
    )
    assert summary(client).runs_by_status == {"RUNNING": 1}
    assert metric("ideaval_trace_bad_events_total") == bad


def test_verdict_approval_fallback_and_verdict_cache_metrics(client: TestClient) -> None:
    names: dict[str, dict[str, str]] = {
        "ideaval_verdict_cost_usd_count": {},
        "ideaval_approvals_total": {"decision": "approved"},
        "ideaval_scorer_fallbacks_total": {"dimension": "learning"},
        "ideaval_cache_requests_total": {"cache": "verdict", "result": "hit"},
    }
    before = {name: metric(name, **labels) for name, labels in names.items()}
    batch = [
        event(
            "run_p",
            1,
            OpsEvent.VERDICT,
            idea_id="i",
            version=1,
            verdict="NO-GO",
            versions=VERSIONS,
        ),
        event("run_p", 2, OpsEvent.APPROVAL, approval_id="ap", decision="approved"),
        event("run_p", 3, OpsEvent.SCORER_FALLBACK, dimension="learning"),
        record("run_p", 4, kind="tool_call", tool="verdict_cache", cache_hit=True),
    ]
    send(client, batch)
    send(client, batch)
    assert {n: metric(n, **labels) - before[n] for n, labels in names.items()} == dict.fromkeys(
        names, 1
    )


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("config/budgets.yaml", "workflows: ["),
        ("config/verdict.yaml", "- just\n- a list\n"),
        ("rubrics/risk.yaml", ""),
        ("rubrics/manifest.yaml", "rubric_set: nope\nrubrics: {}\n"),
        ("prompts/scorer@v1.md", "no front matter"),
        # PyYAML raises a plain ValueError for an impossible unquoted date
        ("config/budgets.yaml", "version: v1\nreviewed: 2026-13-45\n"),
        ("rubrics/risk.yaml", "reviewed: 2026-02-30\n"),
    ],
)
def test_invalid_mounted_file_starts_the_app_but_not_ready(
    settings: AgentOpsSettings, tmp_path: Path, name: str, text: str
) -> None:
    for folder in ("rubrics", "prompts", "config"):
        shutil.copytree(ROOT / folder, tmp_path / folder)
    (tmp_path / name).write_text(text)
    broken = settings.model_copy(
        update={f"{f}_dir": str(tmp_path / f) for f in ("rubrics", "prompts", "config")}
    )
    with TestClient(build_app(broken)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.get("/registry").status_code == 503


def test_migrate_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("AGENTOPS_DATABASE_URL", "POSTGRES_HOST"):
        monkeypatch.delenv(name, raising=False)
    assert migrate.main() == 1
    assert "no database configured" in capsys.readouterr().err
    url = f"sqlite:///{tmp_path}/m.db"
    monkeypatch.setenv("AGENTOPS_DATABASE_URL", url)
    assert migrate.main() == 0
    assert migrate.main() == 0
    migrate.downgrade(make_url(url))
    assert migrate.main() == 0
