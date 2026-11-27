import pytest
from fastapi.testclient import TestClient

from agentops.main import app
from common.contracts.ops import OpsSummary, RegistryResponse, Report

client = TestClient(app)

RECORD = {
    "run_id": "run_1",
    "step_id": 1,
    "agent": "orchestrator",
    "kind": "decision",
    "ts": "2026-11-27T10:00:00Z",
}


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client.get(path).status_code == 200


def test_trace_batch_is_accepted() -> None:
    response = client.post("/trace", json=[RECORD, {**RECORD, "step_id": 2}])
    assert response.status_code == 202 and response.content == b""


def test_invalid_trace_batch_is_rejected() -> None:
    assert client.post("/trace", json=[{**RECORD, "kind": "nope"}]).status_code == 422


def test_read_endpoints_match_contracts() -> None:
    assert client.get("/runs/run_1/trace").json() == []
    summary = OpsSummary.model_validate_json(client.get("/summary").text)
    assert summary.runs_total == 0
    assert RegistryResponse.model_validate_json(client.get("/registry").text).entries == []
    for path, kind in (("/drift/latest", "drift"), ("/eval/latest", "eval")):
        assert Report.model_validate_json(client.get(path).text).kind == kind
