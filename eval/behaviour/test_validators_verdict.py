"""The four M9 responses, merged as M3 will merge them, are a valid M11 request and the gate
inputs act as intended. M11 is the real engine with ``config/verdict.yaml``; M10 is a stub."""

import json
from typing import Any

import httpx
import pytest
from validator_factories import ROOT, StubScorer, body, engine, kb, profile

from common.contracts.agents import ScorerRequest, ScorerResponse
from common.contracts.core import GateInputs, GateResult, Verdict, VerdictLabel, VerdictRequest
from common.llm import Cassette
from common.settings import LlmSettings
from scorer.main import build_app as build_scorer_app
from scorer.main import build_scorer
from scorer.settings import ScorerSettings
from validators.scorers import SCORERS
from verdict.config import load_config
from verdict.engine import decide

CONFIG = load_config(ROOT / "config/verdict.yaml")


async def merged(request: ScorerRequest, **built: Any) -> VerdictRequest:
    shared = engine(**built)
    responses: list[ScorerResponse] = [await run(shared, request) for run in SCORERS.values()]
    gate_inputs: dict[str, Any] = {}
    for response in responses:
        own = response.gate_inputs.model_dump(exclude_unset=True)
        assert not gate_inputs.keys() & own.keys(), "two scorers fill the same gate input"
        gate_inputs |= own
    return VerdictRequest(
        idea_id="idea_1",
        version=1,
        run_id=request.run_id,
        goal=request.profile.goal,
        profile=request.profile,
        scores=[s for r in responses for s in r.scores],
        gate_inputs=GateInputs.model_validate(gate_inputs),
        rubric_set="r1",
    )


def judge(request: VerdictRequest, **gate_changes: Any) -> Verdict:
    gates = request.gate_inputs.model_copy(update=gate_changes)
    return decide(request.model_copy(update={"gate_inputs": gates}), CONFIG)


# the evidence gate comes first; this lifts it so that the later gates can be observed
ENOUGH = {"n_sources": 5, "n_platforms": 3}
TOP = {"score": 5.0, "confidence": 0.9}


@pytest.mark.parametrize(
    "idea", ["flashcards", "flaky_tests", "gym_log", "slidecards_clone", "symptom_bot"]
)
async def test_four_responses_merge_into_a_valid_verdict_request(idea: str) -> None:
    result = decide(await merged(body(idea), scorer=StubScorer(**TOP)), CONFIG)
    assert result.verdict in set(VerdictLabel)


async def test_a_strong_harmless_idea_can_reach_go() -> None:
    """Positive control: without it the 'never GO' tests below would prove nothing."""
    request = await merged(body("flashcards"), scorer=StubScorer(**TOP))
    assert judge(request, **ENOUGH).verdict is VerdictLabel.GO


async def test_empty_kb_gives_insufficient_evidence() -> None:
    request = await merged(body("flashcards"), knowledge=kb([]))
    assert decide(request, CONFIG).verdict is VerdictLabel.INSUFFICIENT_EVIDENCE


async def test_named_blocker_gives_no_go() -> None:
    request = await merged(body("symptom_bot"), scorer=StubScorer(**TOP))
    result = judge(request, **ENOUGH)
    assert result.verdict is VerdictLabel.NO_GO and result.gates.risk is GateResult.FAIL
    assert "medical advice" in result.gate_details["risk"]


async def test_unconfirmed_blocker_can_never_give_go() -> None:
    knowledge = kb(c for c in kb().cards.values() if "health" not in c.niche_tags)
    idea = body("flashcards").idea
    risky = idea.model_copy(update={"text": idea.text + " It also gives a diagnosis."})
    request = await merged(body(idea=risky), knowledge=knowledge, scorer=StubScorer(**TOP))
    assert request.gate_inputs.blockers == []
    result = judge(request, **ENOUGH)
    assert result.verdict is not VerdictLabel.GO


async def test_clone_without_differentiation_fails_the_duplicate_gate() -> None:
    request = await merged(body("slidecards_clone"), scorer=StubScorer(score=2.0, confidence=0.9))
    result = judge(request, **ENOUGH)
    assert result.gates.duplicate is GateResult.FAIL and result.verdict is VerdictLabel.NO_GO
    assert "SlideCards (card_816599ab)" in result.gate_details["duplicate"]


async def test_too_few_hours_fails_the_effort_gate_with_the_code_band() -> None:
    who = profile(hours_per_week=1, weeks_available=2, team_size=1)
    request = await merged(body("flashcards", profile=who), scorer=StubScorer(**TOP))
    result = judge(request, **ENOUGH)
    assert result.gates.feasibility is GateResult.FAIL and result.verdict is VerdictLabel.NO_GO


async def test_failed_scorer_model_never_gives_go() -> None:
    failed = {"score": None, "confidence": 0, "evidence_ids": [], "model": "none"}
    request = await merged(body("flashcards"), scorer=StubScorer(**failed))
    assert judge(request, **ENOUGH).verdict is VerdictLabel.INSUFFICIENT_EVIDENCE


async def test_real_scorer_accepts_all_six_requests() -> None:
    """AC 7 for every dimension: the real M10 app, in process, with a cassette as its model."""
    ids = [f"{card_id}#c0" for card_id in kb().cards]

    class CitesAll(Cassette):  # M10 strips the ids it was not sent
        def next(self, alias: str, agent: str) -> dict[str, str]:
            reply = {"score": 3.5, "confidence": 0.6, "evidence_ids": ids, "reason": "ok"}
            return {"content": json.dumps(reply)}

    settings = ScorerSettings(
        backend="hosted", rubrics_dir=str(ROOT / "rubrics"), prompts_dir=str(ROOT / "prompts")
    )
    scorer = build_scorer(
        settings,
        LlmSettings(
            LLM_BACKEND="fake",
            LLM_CASSETTE_PATH=str(ROOT / "eval/behaviour/cassettes/validators_effort.jsonl"),
        ),
    )
    assert scorer is not None and scorer.hosted is not None
    scorer.hosted._cassette = CitesAll([])
    transport = httpx.ASGITransport(build_scorer_app(settings, scorer=scorer))
    request = await merged(body("flashcards"), transport=transport)
    assert [(s.score, s.model) for s in request.scores] == [(3.5, "scorer-hosted")] * 6
