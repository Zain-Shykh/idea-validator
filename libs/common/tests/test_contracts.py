"""Guide example JSONs (requirements §5, DATA-1..8) and invalid variants."""

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from common.contracts.agents import FineTuneExample, Rubric
from common.contracts.core import (
    Dimension,
    DimensionScore,
    EvidenceItem,
    IdeaCard,
    StudentProfile,
    TraceRecord,
    Verdict,
)

CARD = {
    "id": "card_8f21", "name": "ExampleApp", "summary": "One line on what it does",
    "problem": "...", "customer": "...", "niche_tags": ["devtools"],
    "stage": "launched | seed | ... | unknown", "funding_usd": 1500000,
    "source": "hackernews", "url": "https://...", "published_at": "2026-03-02",
    "fetched_at": "2026-11-10", "text": "Cleaned text used for embeddings",
}  # fmt: skip
PROFILE = {
    "user_id": "u_12",
    "skills": ["python", "react"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "startup",
    "interests": ["education"],
}  # fmt: skip  (the Guide writes the allowed compute/goal values as "a | b | c")
EVIDENCE = {
    "evidence_id": "card_8f21#c2", "card_id": "card_8f21", "quote": "Export is slow",
    "source": "hackernews", "url": "https://...", "retrieved_at": "2026-11-10T09:00:00Z",
}  # fmt: skip
SCORE = {
    "dimension": "demand_pain", "score": 3.5, "confidence": 0.7,
    "evidence_ids": ["card_8f21#c2", "card_77a1#c0"],
    "features": {"n_sources": 4, "trend_growth": 0.31},
    "reason": "Short explanation that cites the ids above",
    "rubric_version": "demand_pain@v3", "model": "scorer-lora@v2",
}  # fmt: skip


def six_scores() -> list[dict[str, Any]]:
    return [{**SCORE, "dimension": d.value, "rubric_version": f"{d.value}@v1"} for d in Dimension]


VERDICT = {
    "idea_id": "idea_5", "version": 2, "run_id": "run_90",
    "verdict": "GO",
    "stability": "STABLE", "score": 70.5, "confidence": 0.74,
    "gates": {"evidence": "pass", "feasibility": "pass", "risk": "pass", "duplicate": "pass"},
    "weights": {"demand_pain": 15, "competition": 15, "differentiation": 15,
                "feasibility": 25, "risk": 10, "learning": 20},
    "thresholds": {"T_go": 70, "T_no": 50, "floor": 1.5},
    "scores": six_scores(), "weakest_dimension": "competition",
    "what_would_make_go": [
        {"change": "Narrow to X segment", "new_score": 73.5, "new_verdict": "GO"}
    ],
    "sensitivity": {"runs": 14, "flipped": 3},
    "versions": {"engine": "m11@v1", "rubrics": "r5", "profile_weights": "w2"},
}  # fmt: skip
TRACE = {
    "run_id": "run_90", "step_id": 12, "parent_step": 9, "agent": "competition_scorer",
    "kind": "tool_call", "tool": "search_kb", "input": "...", "output": "...",
    "tokens_in": 812, "tokens_out": 140, "cost_usd": 0.0004, "latency_ms": 930,
    "retries": 0, "model": "scorer-lora@v2", "cache_hit": False, "error": None,
    "ts": "2026-11-10T09:00:00Z",
}  # fmt: skip
FINETUNE = {"messages": [
    {"role": "system",
     "content": "Score the idea on demand_pain using rubric demand_pain@v3. Reply with JSON only."},
    {"role": "user", "content": "IDEA: ... EVIDENCE: [card_8f21#c2] ... [card_77a1#c0] ..."},
    {"role": "assistant",
     "content": '{"score": 3.5, "confidence": 0.7, '
                '"evidence_ids": ["card_8f21#c2"], "reason": "..."}'},
]}  # fmt: skip
RUBRIC = {
    "dimension": "demand_pain",
    "version": 3,
    "scale": "0-5",
    "anchors": {level: f"anchor {level}" for level in range(6)},
    "features": ["n_sources", "n_platforms", "complaint_count", "trend_growth"],
    "output_schema": "score, confidence, evidence_ids, reason",
    "examples": [{"input": "...", "expected": {"score": 5, "evidence_ids": ["..."]}}],
}


@pytest.mark.parametrize(
    ("model", "example"),
    [
        (IdeaCard, CARD),
        (StudentProfile, PROFILE),
        (EvidenceItem, EVIDENCE),
        (DimensionScore, SCORE),
        (Verdict, VERDICT),
        (TraceRecord, TRACE),
        (FineTuneExample, FINETUNE),
        (Rubric, RUBRIC),
    ],
    ids=lambda v: v.__name__ if isinstance(v, type) else "",
)
def test_guide_example_validates(model: type[BaseModel], example: dict[str, Any]) -> None:
    parsed = model.model_validate_json(json.dumps(example))
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (DimensionScore, {**SCORE, "evidence_ids": []}),
        (DimensionScore, {k: v for k, v in SCORE.items() if k != "evidence_ids"}),
        (DimensionScore, {**SCORE, "score": 5.5}),
        (DimensionScore, {**SCORE, "score": float("nan")}),
        (DimensionScore, {**SCORE, "confidence": 1.2}),
        (DimensionScore, {**SCORE, "dimension": "virality"}),
        (DimensionScore, {**SCORE, "evidence_ids": ["card_8f21"]}),
        (DimensionScore, {**SCORE, "score": None, "confidence": 0.5}),
        (DimensionScore, {**SCORE, "rubric_version": "risk@v1"}),
        (DimensionScore, {**SCORE, "surprise": 1}),
        (EvidenceItem, {**EVIDENCE, "card_id": "card_other"}),
        (Verdict, {**VERDICT, "scores": six_scores()[:5]}),
        (Verdict, {**VERDICT, "scores": [*six_scores()[:5], six_scores()[0]]}),
        (Verdict, {**VERDICT, "weights": {**VERDICT["weights"], "risk": 11}}),
        (Verdict, {**VERDICT, "verdict": "INSUFFICIENT_EVIDENCE"}),
        (Verdict, {**VERDICT, "thresholds": {"T_go": 40, "T_no": 50, "floor": 1.5}}),
        (Verdict, {**VERDICT, "verdict": "MAYBE"}),
        (Rubric, {**RUBRIC, "anchors": {5: "only one"}}),
        (FineTuneExample, {"messages": FINETUNE["messages"][:2]}),
    ],
)
def test_invalid_variant_rejected(model: type[BaseModel], payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_abstained_score_is_valid() -> None:
    abstained = DimensionScore.model_validate(
        {**SCORE, "score": None, "confidence": 0, "evidence_ids": [], "reason": "no evidence"}
    )
    assert abstained.score is None


def test_what_would_make_go_uses_from_and_to_keys() -> None:
    change = {"change": "x", "new_score": 73.5, "new_verdict": "GO", "from": 2.0, "to": 3.0}
    verdict = Verdict.model_validate({**VERDICT, "what_would_make_go": [change]})
    dumped = verdict.model_dump(mode="json")["what_would_make_go"][0]
    assert (dumped["from"], dumped["to"]) == (2.0, 3.0)
