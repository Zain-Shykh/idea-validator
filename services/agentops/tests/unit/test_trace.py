from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy.exc import OperationalError
from trace_factories import event, record, thousand_records, wire

from agentops.services.ingest import TraceService
from agentops.settings import AgentOpsSettings
from common.contracts.core import TraceRecord
from common.contracts.ops import OpsEvent, OpsSummary


def service(client: TestClient) -> TraceService:
    traces: TraceService = client.app.state.traces  # type: ignore[attr-defined]
    return traces


def metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def read(client: TestClient, run_id: str) -> list[TraceRecord]:
    response = client.get(f"/runs/{run_id}/trace")
    assert response.status_code == 200
    return [TraceRecord.model_validate(r) for r in response.json()]


def post(client: TestClient, records: list[TraceRecord], size: int = 20) -> None:
    for start in range(0, len(records), size):
        response = client.post("/trace", json=wire(records[start : start + size]))
        assert response.status_code == 202, response.text


def test_thousand_records_ten_runs(client: TestClient, settings: AgentOpsSettings) -> None:
    records = thousand_records()
    post(client, records, size=50)
    post(client, records[:200], size=50)  # at-least-once delivery
    files = sorted(Path(settings.traces_dir).glob("2026-11-27/*.jsonl"))
    assert [f.stem for f in files] == [f"run_{i}" for i in range(10)]
    index = service(client).index
    assert index is not None
    for run in range(10):
        stored = read(client, f"run_{run}")
        assert [r.step_id for r in stored] == list(range(1, 101))
        assert [r.parent_step for r in stored] == [None, *range(1, 100)]
        row = index.get(f"run_{run}")
        assert row is not None
        assert (row.steps, row.tokens_in, row.tokens_out) == (100, 1000, 200)
        assert row.cost_usd == pytest.approx(98 * 0.0001)
        # steps 4, 8, ... 96
        assert (row.cache_hits, row.fallbacks) == (24, 2)
        assert (row.status, row.latency_ms) == ("RUNNING", 99_000)
    assert len(index.all()) == 10


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'[{"run_id": "run_1"}]',
        b'[{"run_id":"r","step_id":1,"agent":"a","kind":"nope","ts":"2026-11-27T10:00:00Z"}]',
    ]
    + [
        b'[{"run_id":"r","step_id":1,"agent":"a","kind":"decision",'
        b'"ts":"2026-11-27T10:00:00Z",' + extra + b"}]"
        for extra in (
            b'"surprise":1',
            b'"cost_usd":NaN',
            b'"cost_usd":1e300',
            b'"tokens_in":99999999999999999999',
            b'"input":"\\ud800"',
        )
    ],
)
def test_malformed_batches_are_rejected_and_counted(client: TestClient, body: bytes) -> None:
    before = metric("ideaval_trace_rejected_total")
    response = client.post("/trace", content=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert metric("ideaval_trace_rejected_total") > before
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "../escape"},
        {"run_id": "a/b"},
        {"run_id": "*"},
        {"run_id": "x" * 129},
        {"agent": "a\u0000"},
        {"step_id": 2**60},
        {"ts": "0001-01-01T00:00:00+05:00"},
        {"ts": "9999-12-31T23:59:59Z"},
    ],
)
def test_invalid_batches_are_rejected_and_counted(
    client: TestClient, settings: AgentOpsSettings, change: dict[str, object]
) -> None:
    before = metric("ideaval_trace_rejected_total")
    good, bad = wire([record(step_id=1), record(step_id=2)])
    response = client.post("/trace", json=[good, bad | change])
    assert response.status_code == 422, response.text
    assert metric("ideaval_trace_rejected_total") == before + 1
    assert not Path(settings.traces_dir).exists()  # all or nothing
    post(client, [record()])


def test_rejection_does_not_echo_the_record(client: TestClient) -> None:
    bad = wire([record()])[0] | {"input": {"secret": "student data"}}
    assert "student data" not in client.post("/trace", json=[bad]).text


def test_too_many_records_and_too_many_bytes(client: TestClient) -> None:
    many = wire([record(step_id=i) for i in range(1001)])
    assert client.post("/trace", json=many).status_code == 422
    assert client.post("/trace", content=b"x" * 5_000_001).status_code == 413


@pytest.mark.parametrize("run_id", ["a%20b", "x" * 129, "*"])
def test_trace_read_rejects_unsafe_ids(client: TestClient, run_id: str) -> None:
    assert client.get(f"/runs/{run_id}/trace").status_code == 422


def test_naive_timestamps_are_utc_and_sortable(client: TestClient) -> None:
    naive = wire([record(step_id=2)])[0] | {"ts": "2026-11-27T09:00:00"}
    assert client.post("/trace", json=[naive, *wire([record(step_id=1)])]).status_code == 202
    assert [r.step_id for r in read(client, "run_1")] == [2, 1]
    assert client.get("/summary").status_code == 200


def test_run_over_midnight_uses_two_files(client: TestClient, settings: AgentOpsSettings) -> None:
    post(
        client,
        [
            record(step_id=2, ts="2026-11-28T00:00:01Z"),
            record(step_id=1, ts="2026-11-27T23:59:59Z"),
        ],
    )
    assert len(list(Path(settings.traces_dir).glob("*/run_1.jsonl"))) == 2
    assert [r.step_id for r in read(client, "run_1")] == [1, 2]


def test_half_written_line_does_not_swallow_the_next_record(
    client: TestClient, settings: AgentOpsSettings
) -> None:
    post(client, [record(step_id=1)])
    path = Path(settings.traces_dir) / "2026-11-27/run_1.jsonl"
    path.write_bytes(path.read_bytes() + b'{"run_id": "run_1", "ste')
    post(client, [record(step_id=2)])
    assert [r.step_id for r in read(client, "run_1")] == [1, 2]


def test_database_outage_is_503_and_the_retry_is_not_double_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = service(client).index
    assert index is not None
    batch = [event("run_1", 1, OpsEvent.INJECTION_BLOCKED, stage="m12", count=2)]
    before = metric("ideaval_injections_blocked_total", stage="m12")
    with monkeypatch.context() as patch:

        def down(_: object) -> None:
            raise OperationalError("insert", {}, Exception("secret value"))

        patch.setattr(index, "upsert", down)
        response = client.post("/trace", json=wire(batch))
        assert response.status_code == 503 and "secret" not in response.text
    post(client, batch)
    post(client, batch)
    assert metric("ideaval_injections_blocked_total", stage="m12") == before + 2
    assert OpsSummary.model_validate(client.get("/summary").json()).blocked_injections == 2


def test_unwritable_trace_volume_is_503_and_not_ready(settings: AgentOpsSettings) -> None:
    from agentops.main import build_app

    Path(settings.traces_dir).write_text("a file where the directory should be")
    with TestClient(build_app(settings)) as client:
        assert client.post("/trace", json=wire([record()])).status_code == 503
        assert client.get("/ready").status_code == 503


@pytest.mark.parametrize("output", ["not json", None, '{"status": "DONE"}'])
def test_bad_event_payload_is_stored_ignored_and_counted(
    client: TestClient, output: str | None
) -> None:
    before = metric("ideaval_trace_bad_events_total")
    post(client, [record(tool="run_status", output=output)])
    assert metric("ideaval_trace_bad_events_total") == before + 1
    assert len(read(client, "run_1")) == 1
    assert client.get("/summary").json()["runs_by_status"] == {"RUNNING": 1}


def test_nul_in_an_event_payload_never_reaches_the_database(client: TestClient) -> None:
    payload = '{"workflow": "validate", "user_id": "u\\u0000"}'
    post(client, [record(tool="run_started", output=payload)])
    index = service(client).index
    assert index is not None
    row = index.get("run_1")
    assert row is not None and row.user_id is None


def test_metrics_follow_the_records(client: TestClient) -> None:
    finished = event("run_m", 2, OpsEvent.RUN_STATUS, status="BUDGET_STOPPED")
    call = record(
        "run_m",
        1,
        agent="demand_scorer",
        kind="llm_call",
        model="scorer-base@v1",
        tokens_in=7,
        latency_ms=500,
    )
    names = {
        "ideaval_runs_total": {"workflow": "unknown", "status": "BUDGET_STOPPED"},
        "ideaval_budget_stops_total": {},
        "ideaval_llm_tokens_total": {"model": "scorer-base@v1", "direction": "in"},
        "ideaval_cache_requests_total": {"cache": "llm", "result": "miss"},
        "ideaval_agent_latency_seconds_count": {"agent": "demand_scorer"},
    }
    before = {name: metric(name, **labels) for name, labels in names.items()}
    post(client, [call, finished])
    post(client, [call, finished])
    added = {name: metric(name, **labels) - before[name] for name, labels in names.items()}
    assert added == {
        "ideaval_runs_total": 1,
        "ideaval_budget_stops_total": 1,
        "ideaval_llm_tokens_total": 7,
        "ideaval_cache_requests_total": 1,
        "ideaval_agent_latency_seconds_count": 1,
    }


async def test_the_shared_trace_client_delivers_to_this_api(client: TestClient) -> None:
    import io

    import httpx

    from common.contracts.core import TraceKind
    from common.http import ServiceClient
    from common.trace import TraceClient

    transport = httpx.ASGITransport(app=client.app)
    async with ServiceClient("http://agentops", transport=transport) as http:

        async def sink(batch: list[TraceRecord]) -> None:
            await http.post("/trace", batch)

        tracer = TraceClient(sink=sink, stream=io.StringIO())
        with (
            tracer.step("orchestrator", TraceKind.DECISION, run_id="run_t") as outer,
            tracer.step("critic", TraceKind.LLM_CALL, run_id="run_t", input="full prompt"),
        ):
            pass
        await tracer.flush()
    child, parent = sorted(read(client, "run_t"), key=lambda r: r.agent)
    assert child.parent_step == outer.step_id == parent.step_id
    assert child.input == "full prompt"  # M13 keeps what stdout redacts (D-002)
