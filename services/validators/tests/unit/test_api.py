import pytest
from fastapi.testclient import TestClient

from common.contracts.agents import ScorerResponse
from common.contracts.core import Dimension
from validators.main import app

client = TestClient(app)

BODY = {
    "run_id": "r",
    "idea": {"title": "t", "text": "x"},
    "profile": {
        "user_id": "u_1",
        "skills": [],
        "hours_per_week": 10,
        "weeks_available": 12,
        "team_size": 1,
        "compute": "laptop",
        "goal": "portfolio",
        "interests": [],
    },
    "evidence": [],
}


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client.get(path).status_code == 200


def test_scorers_abstain_and_cover_all_six_dimensions() -> None:
    scores = []
    for scorer in ("demand", "competition", "feasibility", "risk"):
        response = client.post(f"/score/{scorer}", json=BODY)
        assert response.status_code == 200
        scores += ScorerResponse.model_validate_json(response.text).scores
    assert sorted(s.dimension for s in scores) == sorted(Dimension)
    assert all(s.score is None and s.confidence == 0 and not s.evidence_ids for s in scores)


def test_unknown_scorer_is_404() -> None:
    response = client.post("/score/vibes", json=BODY)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
