from typing import Any

import pytest
from fastapi.testclient import TestClient

from common.contracts.core import Verdict, VerdictLabel
from common.contracts.verdict import VerdictConfigResponse, WhatIfResponse
from verdict.main import app

client = TestClient(app)


@pytest.fixture
def request_body() -> dict[str, Any]:
    return {
        "idea_id": "idea_1",
        "version": 2,
        "run_id": "run_1",
        "goal": "portfolio",
        "profile": {
            "user_id": "u_1",
            "skills": ["python"],
            "hours_per_week": 10,
            "weeks_available": 12,
            "team_size": 2,
            "compute": "laptop",
            "goal": "portfolio",
            "interests": [],
        },
        # strong scores everywhere: the stub must still not decide GO
        "scores": [
            {
                "dimension": d,
                "score": 5,
                "confidence": 1,
                "evidence_ids": ["card_ab#c0"],
                "reason": "r",
                "rubric_version": f"{d}@v1",
                "model": "m",
            }
            for d in (
                "demand_pain",
                "competition",
                "differentiation",
                "feasibility",
                "risk",
                "learning",
            )
        ],
        "gate_inputs": {
            "n_sources": 9,
            "n_platforms": 3,
            "est_effort_hours": 10,
            "available_hours": 240,
        },
        "rubric_set": "r1",
        "kb_version": "kb1",
    }


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client.get(path).status_code == 200


def test_stub_verdict_is_insufficient_evidence(request_body: dict[str, Any]) -> None:
    response = client.post("/verdict", json=request_body)
    assert response.status_code == 200
    verdict = Verdict.model_validate_json(response.text)
    assert verdict.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE
    assert verdict.score is None and verdict.missing
    assert (verdict.idea_id, verdict.version, verdict.run_id) == ("idea_1", 2, "run_1")
    assert verdict.versions.rubrics == "r1" and verdict.versions.kb_version == "kb1"


def test_invalid_request_gets_error_envelope(request_body: dict[str, Any]) -> None:
    request_body["scores"] = request_body["scores"][:5]
    response = client.post("/verdict", json=request_body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_whatif_modes(request_body: dict[str, Any]) -> None:
    search = client.post("/whatif", json={**request_body, "mode": "search"})
    assert WhatIfResponse.model_validate_json(search.text) == WhatIfResponse()
    apply = client.post(
        "/whatif",
        json={**request_body, "mode": "apply", "changes": [{"dimension": "risk", "to": 5}]},
    )
    hypothetical = WhatIfResponse.model_validate_json(apply.text).verdict
    assert hypothetical and hypothetical.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE


def test_config_reports_stub_versions() -> None:
    response = client.get("/config")
    assert VerdictConfigResponse.model_validate_json(response.text).versions.engine == "m11@stub"
