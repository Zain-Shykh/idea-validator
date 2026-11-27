import pytest
from fastapi.testclient import TestClient

from common.contracts.agents import (
    ApprovalPolicyResponse,
    CheckResponse,
    InputRailResponse,
    ScanResponse,
)
from critic.main import app

client = TestClient(app)


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client.get(path).status_code == 200


def test_check_marks_every_claim_unverifiable() -> None:
    claims = [{"claim_id": f"c{i}", "text": "t", "evidence_ids": ["card_ab#c0"]} for i in (1, 2)]
    response = client.post("/check", json={"claims": claims})
    results = CheckResponse.model_validate_json(response.text).results
    assert [r.claim_id for r in results] == ["c1", "c2"]
    assert all(not r.supported and r.verdict == "unverifiable" for r in results)


def test_input_rail_says_it_did_not_check() -> None:
    response = client.post("/rail/input", json={"text": "hello"})
    result = InputRailResponse.model_validate_json(response.text)
    assert result.allowed and result.redacted_text == "hello" and "stub" in result.reasons[0]


def test_scan_and_approval_policy() -> None:
    scan = client.post("/scan", json={"evidence": []})
    assert ScanResponse.model_validate_json(scan.text).flagged == []
    body = {
        "run_id": "r",
        "action": "deep_search",
        "estimate": {},
        "remaining": {"max_tokens": 0, "max_cost_usd": 0, "max_pages": 0, "max_steps": 0},
    }
    response = client.post("/policy/approval", json=body)
    assert ApprovalPolicyResponse.model_validate_json(response.text).requires_approval
