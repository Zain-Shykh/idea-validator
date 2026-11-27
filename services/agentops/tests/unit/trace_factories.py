"""Builders for trace records (importlib mode: shared helpers cannot live in conftest)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from common.contracts.core import TraceRecord
from common.contracts.ops import OPS_EVENT_PAYLOADS, OpsEvent

T0 = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
VERSIONS = {"engine": "m11@v1", "rubrics": "r1", "profile_weights": "w1"}


def record(run_id: str = "run_1", step_id: int = 1, **fields: Any) -> TraceRecord:
    base: dict[str, Any] = {
        "run_id": run_id,
        "step_id": step_id,
        "agent": "orchestrator",
        "kind": "decision",
        "ts": T0 + timedelta(seconds=step_id),
    }
    return TraceRecord.model_validate(base | fields)


def event(run_id: str, step_id: int, name: OpsEvent, **payload: Any) -> TraceRecord:
    body = OPS_EVENT_PAYLOADS[name].model_validate(payload).model_dump_json()
    return record(run_id, step_id, tool=name.value, output=body)


def wire(records: list[TraceRecord]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in records]


def seeded_runs() -> list[TraceRecord]:
    """Two runs with hand-computed totals (test_summary)."""

    def call(run: str, step: int, agent: str, ms: int, **fields: Any) -> TraceRecord:
        fields = {"kind": "llm_call"} | fields
        return record(run, step, agent=agent, latency_ms=ms, **fields)

    first = [
        event("run_a", 1, OpsEvent.RUN_STARTED, workflow="validate", user_id="u_1"),
        call("run_a", 2, "demand_scorer", 100, cost_usd=0.001, tokens_in=100, tokens_out=10),
        call("run_a", 3, "demand_scorer", 300, cost_usd=0.002, tokens_in=200, tokens_out=20),
        call("run_a", 4, "critic", 50, kind="tool_call", tool="search_kb", cache_hit=True),
        event("run_a", 5, OpsEvent.SCORER_FALLBACK, dimension="risk"),
        event("run_a", 6, OpsEvent.APPROVAL, approval_id="ap_1", decision="pending"),
        event("run_a", 7, OpsEvent.APPROVAL, approval_id="ap_1", decision="approved"),
        event("run_a", 8, OpsEvent.INJECTION_BLOCKED, stage="m5", count=3),
        event(
            "run_a",
            9,
            OpsEvent.VERDICT,
            idea_id="idea_1",
            version=1,
            verdict="GO",
            versions=VERSIONS,
        ),
        event("run_a", 10, OpsEvent.RUN_STATUS, status="COMPLETED"),
    ]
    second = [
        event("run_b", 1, OpsEvent.RUN_STARTED, workflow="discover", user_id="u_2"),
        call("run_b", 2, "demand_scorer", 200, cost_usd=0.006, cache_hit=True),
        call("run_b", 3, "demand_scorer", 1000),
        event(
            "run_b",
            4,
            OpsEvent.VERDICT,
            idea_id="idea_2",
            version=1,
            verdict="PIVOT",
            versions=VERSIONS,
        ),
        event(
            "run_b",
            5,
            OpsEvent.VERDICT,
            idea_id="idea_3",
            version=1,
            verdict="PIVOT",
            versions=VERSIONS,
        ),
        event("run_b", 6, OpsEvent.APPROVAL, approval_id="ap_1", decision="declined"),
        event("run_b", 7, OpsEvent.APPROVAL, approval_id="ap_2", decision="pending"),
        event("run_b", 8, OpsEvent.INJECTION_BLOCKED, stage="m12", count=1),
        event("run_b", 9, OpsEvent.RUN_STATUS, status="BUDGET_STOPPED"),
    ]
    return first + second


def thousand_records() -> list[TraceRecord]:
    """100 records for each of 10 runs, interleaved and out of order, as several pods would."""
    records = []
    for step in range(100, 0, -1):
        for run in range(10):
            records.append(
                record(
                    f"run_{run}",
                    step,
                    parent_step=step - 1 if step > 1 else None,
                    kind="llm_call",
                    agent=f"agent_{step % 3}",
                    tokens_in=10,
                    tokens_out=2,
                    cost_usd=0.0001,
                    latency_ms=step,
                    cache_hit=step % 4 == 0,
                )
            )
    # steps 99 and 100 of each run become fallbacks (decision records carry no call)
    return [
        event(r.run_id, r.step_id, OpsEvent.SCORER_FALLBACK, dimension="risk").model_copy(
            update={"parent_step": r.parent_step, "tokens_in": 10, "tokens_out": 2}
        )
        if r.step_id > 98
        else r
        for r in records
    ]
