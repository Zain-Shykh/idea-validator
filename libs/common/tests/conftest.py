from typing import Any

import pytest

DIMENSIONS = (
    "demand_pain",
    "competition",
    "differentiation",
    "feasibility",
    "risk",
    "learning",
)


@pytest.fixture
def profile() -> dict[str, Any]:
    return {
        "user_id": "u_12",
        "skills": ["python"],
        "hours_per_week": 10,
        "weeks_available": 12,
        "team_size": 2,
        "compute": "laptop",
        "goal": "portfolio",
        "interests": ["education"],
    }


@pytest.fixture
def verdict_request(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "idea_id": "idea_1",
        "version": 1,
        "run_id": "run_1",
        "goal": "portfolio",
        "profile": profile,
        "scores": [
            {
                "dimension": d,
                "score": 3.0,
                "confidence": 0.7,
                "evidence_ids": ["card_ab#c0"],
                "reason": "r",
                "rubric_version": f"{d}@v1",
                "model": "m",
            }
            for d in DIMENSIONS
        ],
        "gate_inputs": {
            "n_sources": 3,
            "n_platforms": 2,
            "est_effort_hours": 100,
            "available_hours": 240,
        },
        "rubric_set": "r1",
    }
