from pathlib import Path
from typing import Any

from common.contracts.core import Dimension, VerdictRequest

CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "verdict.yaml"

# VE-16, in Dimension order. Risk is the least confident, so the score runs move risk.
WORKED_SCORES = (3.5, 2.0, 3.0, 4.0, 3.5, 4.5)
WORKED_CONFIDENCES = (0.75, 0.7, 0.65, 0.75, 0.6, 0.7)


def body(
    scores: tuple[float | None, ...] = WORKED_SCORES,
    confidences: tuple[float, ...] = WORKED_CONFIDENCES,
    goal: str = "portfolio",
    objections: list[dict[str, Any]] | None = None,
    **gate_inputs: Any,
) -> dict[str, Any]:
    return {
        "idea_id": "idea_1",
        "version": 1,
        "run_id": "run_1",
        "goal": goal,
        "profile": {
            "user_id": "u_1",
            "skills": ["python"],
            "hours_per_week": 10,
            "weeks_available": 12,
            "team_size": 2,
            "compute": "laptop",
            "goal": goal,
            "interests": [],
        },
        "scores": [
            {
                "dimension": d.value,
                "score": s,
                "confidence": 0 if s is None else c,
                "evidence_ids": [] if s is None else ["card_ab#c0"],
                "reason": "r",
                "rubric_version": f"{d.value}@v1",
                "model": "m",
            }
            for d, s, c in zip(Dimension, scores, confidences, strict=True)
        ],
        "gate_inputs": {
            "n_sources": 5,
            "n_platforms": 3,
            "est_effort_hours": 100,
            "available_hours": 240,
            **gate_inputs,
        },
        "accepted_objections": objections or [],
        "rubric_set": "r1",
        "kb_version": "kb1",
    }


def make_request(**kwargs: Any) -> VerdictRequest:
    return VerdictRequest.model_validate(body(**kwargs))


def objection(n: int, dimension: str = "competition") -> dict[str, Any]:
    return {
        "objection_id": f"obj_{n}",
        "dimension": dimension,
        "text": "t",
        "evidence_ids": ["card_ab#c0"],
    }


def duplicate(**overrides: Any) -> dict[str, Any]:
    return {
        "card_id": "card_dup",
        "product_name": "Acme Planner",
        "same_core_features": True,
        "same_user": True,
        "similarity": 0.9,
        **overrides,
    }
