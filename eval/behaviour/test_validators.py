"""M9 behaviour against the fake stack (spec Z5, A4 2b, 2c, 5, 6a, 7). No real model is called."""

import asyncio
import json
import time

import httpx
from validator_factories import ROOT, StubScorer, body, engine, kb, llm, profile

from common.contracts.agents import ScorerResponse
from common.contracts.core import Dimension, TraceKind, TraceRecord
from common.llm import Cassette, LLMClient
from common.settings import LlmSettings
from common.trace import TraceClient
from scorer.main import build_app as build_scorer_app
from scorer.main import build_scorer
from scorer.settings import ScorerSettings
from validators.main import build_app as build_validators_app
from validators.scorers import SCORERS
from validators.services.engine import Engine
from validators.settings import ValidatorSettings

EFFORT_CASSETTE = ROOT / "eval/behaviour/cassettes/validators_effort.jsonl"


async def run_all(built: Engine, **changes: object) -> dict[str, ScorerResponse]:
    return {name: await run(built, body(**changes)) for name, run in SCORERS.items()}


async def test_empty_source_abstains() -> None:
    scorer = StubScorer()
    responses = await run_all(engine(scorer=scorer, knowledge=kb([])))
    scores = [s for r in responses.values() for s in r.scores]
    assert len(scores) == 6 and scorer.requests == []
    for score in scores:
        assert score.score is None and score.confidence == 0 and score.evidence_ids == []
        assert score.reason == "no evidence"
    demand = responses["demand"].gate_inputs
    assert (demand.n_sources, demand.n_platforms) == (0, 0)
    assert responses["competition"].gate_inputs.duplicate is None
    assert responses["feasibility"].gate_inputs.est_effort_hours is not None


async def test_invented_competitor_name_never_leaves_m9() -> None:
    """The base model once named a funded "OpenQuiz" that was in no evidence (spec Z4 C4)."""
    reason = "The funded competitor OpenQuiz dominates (card_invented#c0)."
    knowledge = kb()
    responses = await run_all(engine(scorer=StubScorer(reason=reason), knowledge=knowledge))
    dumped = json.dumps([r.model_dump(mode="json") for r in responses.values()])
    assert "OpenQuiz" not in dumped and "card_invented" not in dumped
    cards = {c.id: c.name for c in knowledge.cards.values()}
    competition = responses["competition"].scores[0]
    named = competition.features["competitors"]
    assert all(cards[c["card_id"]] == c["name"] for c in named)


async def test_profile_changes_only_feasibility_and_learning() -> None:
    other = profile(
        user_id="u_2", skills=["Java"], hours_per_week=3, weeks_available=4, team_size=1,
        compute="paid", goal="startup", interests=["fitness"], version=7,
    )  # fmt: skip

    async def outputs(who: object) -> dict[str, ScorerResponse]:
        model = LLMClient(LlmSettings(LLM_BACKEND="fake"), cassette=Cassette.load(EFFORT_CASSETTE))
        return await run_all(engine(model=model), profile=who)

    before, after = await outputs(profile()), await outputs(other)
    for name in ("demand", "competition", "risk"):
        assert before[name] == after[name], name
    assert before["feasibility"] != after["feasibility"]
    gates = after["feasibility"].gate_inputs
    assert (gates.est_effort_hours, gates.available_hours) == (120, 12)


async def test_four_scorers_in_parallel() -> None:
    delay = 0.25

    async def timed(*names: str) -> float:
        built = engine(scorer=StubScorer(delay_s=delay), scorer_concurrency=6)
        start = time.perf_counter()
        await asyncio.gather(*(SCORERS[name](built, body()) for name in names))
        return time.perf_counter() - start

    slowest = max([await timed(name) for name in SCORERS])
    together = await timed(*SCORERS)
    assert together < 2 * slowest, (together, slowest)


async def test_real_scorer_contract_and_traces() -> None:
    """M9 against the real M10 app, in process, with its model replaced by a cassette."""
    records: list[TraceRecord] = []

    async def sink(batch: list[TraceRecord]) -> None:
        records.extend(batch)

    class Citing(Cassette):
        """Cites the first evidence id the prompt holds, like a well-behaved model."""

        def next(self, alias: str, agent: str) -> dict[str, str]:
            return {"content": json.dumps(reply)}

    scorer_settings = ScorerSettings(
        backend="hosted", rubrics_dir=str(ROOT / "rubrics"), prompts_dir=str(ROOT / "prompts")
    )
    scorer = build_scorer(
        scorer_settings, LlmSettings(LLM_BACKEND="fake", LLM_CASSETTE_PATH=str(EFFORT_CASSETTE))
    )
    assert scorer is not None and scorer.hosted is not None
    scorer.tracer = scorer.hosted._tracer = TraceClient(sink=sink)

    found = await engine().retrieve(body(), "lecture slides flashcards university students")
    cited = next(h.item.evidence_id for h in found if "complaint" in h.card.niche_tags)
    reply = {"score": 3.5, "confidence": 0.6, "evidence_ids": [cited], "reason": "ok"}
    scorer.hosted._cassette = Citing([])

    built = engine(
        transport=httpx.ASGITransport(build_scorer_app(scorer_settings, scorer=scorer)),
        model=llm('{"hours": 120}'),
        records=records,
    )
    m9 = httpx.ASGITransport(build_validators_app(ValidatorSettings(), engine=built))
    async with httpx.AsyncClient(transport=m9, base_url="http://validators:8000") as api:
        payload = body().model_dump(mode="json")
        answers = [await api.post(f"/score/{n}", json=payload) for n in ("demand", "feasibility")]
    (demand,) = ScorerResponse.model_validate_json(answers[0].text).scores
    feasibility = ScorerResponse.model_validate_json(answers[1].text)
    assert all(s.model == "scorer-hosted" for s in feasibility.scores)
    assert (demand.score, demand.evidence_ids) == (3.5, [cited])
    assert demand.model == "scorer-hosted" and demand.dimension is Dimension.DEMAND_PAIN
    await built.tracer.flush()
    await scorer.tracer.flush()

    assert {r.run_id for r in records} == {"run_test"}
    tools = {r.tool for r in records if r.agent == "validators" and r.kind is TraceKind.TOOL_CALL}
    assert tools == {"search_kb", "get_cards", "niches", "score_dimension"}
    models = [r for r in records if r.kind is TraceKind.LLM_CALL]
    assert {(r.agent, r.prompt_version) for r in models} == {
        ("validators", "effort@v1"),
        ("scorer", "scorer@v1"),
    }
