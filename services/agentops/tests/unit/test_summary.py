from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from trace_factories import VERSIONS, seeded_runs, wire

from agentops.services.aggregate import percentile
from agentops.services.metrics import BoundedLabel
from common.contracts.core import RunState, VerdictLabel
from common.contracts.ops import OpsSummary


def test_summary_known_numbers(client: TestClient) -> None:
    assert client.post("/trace", json=wire(seeded_runs())).status_code == 202
    summary = OpsSummary.model_validate_json(client.get("/summary").text)
    assert (summary.runs_total, summary.verdicts_total) == (2, 3)
    assert summary.verdict_counts == {
        VerdictLabel.GO: 1,
        VerdictLabel.PIVOT: 2,
        VerdictLabel.NO_GO: 0,
        VerdictLabel.INSUFFICIENT_EVIDENCE: 0,
    }
    assert summary.total_cost_usd == pytest.approx(0.009)
    assert summary.cost_per_verdict_usd == pytest.approx(0.003)
    critic, demand = summary.agent_latency
    assert (critic.agent, critic.calls, critic.p50_ms, critic.p95_ms) == ("critic", 1, 50, 50)
    assert (demand.agent, demand.calls, demand.avg_ms) == ("demand_scorer", 4, 400)
    assert (demand.p50_ms, demand.p95_ms) == (200, 1000)
    assert summary.cache_hit_rate == pytest.approx(2 / 5)
    assert summary.scorer_fallbacks == 1
    # run_b's ap_1 is a different approval from run_a's ap_1
    assert summary.approvals == {"pending": 1, "approved": 1, "declined": 1}
    assert summary.blocked_injections == 4
    assert summary.budget_stops == 1
    assert summary.runs_by_status == {RunState.COMPLETED: 1, RunState.BUDGET_STOPPED: 1}


def test_index_row_keeps_run_identity_and_versions(client: TestClient) -> None:
    client.post("/trace", json=wire(seeded_runs()))
    row = client.app.state.traces.index.get("run_a")  # type: ignore[attr-defined]
    assert (row.workflow, row.user_id, row.status) == ("validate", "u_1", "COMPLETED")
    assert row.versions == VERSIONS | {"prompts": {}, "kb_version": None}
    assert (row.steps, row.tokens_in, row.tokens_out) == (10, 300, 30)
    assert row.started_at.replace(tzinfo=UTC) == datetime(2026, 11, 27, 10, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("values", "p50", "p95"),
    [([7], 7, 7), ([1, 2], 1, 2), (list(range(1, 101)), 50, 95), (list(range(1, 21)), 10, 19)],
)
def test_nearest_rank_percentiles(values: list[int], p50: int, p95: int) -> None:
    assert (percentile(values, 50), percentile(values, 95)) == (p50, p95)


def test_label_values_are_bounded() -> None:
    label = BoundedLabel(limit=2)
    assert [label(v) for v in ("a", "b", "c", "a", 'x"\n', None)] == [
        "a",
        "b",
        "other",
        "a",
        "other",
        "other",
    ]
