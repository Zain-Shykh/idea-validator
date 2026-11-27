import pytest
from fastapi.testclient import TestClient

from common.contracts.agents import (
    ConcretizeResponse,
    GapAnalyzeResponse,
    IdeateResponse,
    IntakeResponse,
    SkepticResponse,
)
from ideation.main import app

client = TestClient(app)

IDEA = {"title": "t", "text": "x"}
PROFILE = {
    "user_id": "u_1",
    "skills": [],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 1,
    "compute": "laptop",
    "goal": "portfolio",
    "interests": [],
}
EVIDENCE = {
    "evidence_id": "card_ab#c0",
    "card_id": "card_ab",
    "quote": "q",
    "source": "yc",
    "url": "https://example.com",
    "retrieved_at": "2026-11-27",
}


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client.get(path).status_code == 200


def test_intake() -> None:
    response = client.post("/intake", json={"run_id": "r", "idea": IDEA, "profile": PROFILE})
    assert IntakeResponse.model_validate_json(response.text).questions == []


def test_ideas_only_come_from_supplied_gap_evidence() -> None:
    body = {"run_id": "r", "profile": PROFILE, "niches": [], "gaps": [], "round": 1}
    empty = client.post("/ideate", json=body)
    assert IdeateResponse.model_validate_json(empty.text).ideas == []
    gap = {"niche_id": "n1", "statement": "users lack offline mode", "evidence": [EVIDENCE]}
    response = client.post("/ideate", json={**body, "gaps": [gap] * 8})
    ideas = IdeateResponse.model_validate_json(response.text).ideas
    assert len(ideas) == 6
    assert ideas[0].gap_evidence_ids == ["card_ab#c0"]
    enriched = client.post(
        "/gap-analyze", json={"run_id": "r", "ideas": [ideas[0].model_dump(mode="json")]}
    )
    assert GapAnalyzeResponse.model_validate_json(enriched.text).ideas[0].complaints == []


def test_skeptic_and_concretize() -> None:
    skeptic = client.post("/skeptic", json={"run_id": "r", "idea": IDEA, "evidence": []})
    assert SkepticResponse.model_validate_json(skeptic.text).objections == []
    change = {"dimension": "risk", "from_score": 1, "to_score": 3}
    response = client.post("/concretize", json={"run_id": "r", "idea": IDEA, "changes": [change]})
    assert ConcretizeResponse.model_validate_json(response.text).changes[0].dimension == "risk"


def test_invalid_round_is_rejected() -> None:
    body = {"run_id": "r", "profile": PROFILE, "niches": [], "gaps": [], "round": 4}
    assert client.post("/ideate", json=body).status_code == 422
