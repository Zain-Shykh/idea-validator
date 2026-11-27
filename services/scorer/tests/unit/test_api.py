import pytest
from fastapi.testclient import TestClient

from common.contracts.core import DimensionScore
from scorer.main import app

client = TestClient(app)

BODY = {"rubric_id": "risk@v1", "idea": {"title": "t", "text": "x"}, "evidence": []}


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client.get(path).status_code == 200


def test_score_abstains() -> None:
    score = DimensionScore.model_validate_json(client.post("/score", json=BODY).text)
    assert (score.dimension, score.score, score.rubric_version) == ("risk", None, "risk@v1")


@pytest.mark.parametrize("rubric_id", ["vibes@v1", "risk", ""])
def test_bad_rubric_is_rejected(rubric_id: str) -> None:
    response = client.post("/score", json={**BODY, "rubric_id": rubric_id})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
