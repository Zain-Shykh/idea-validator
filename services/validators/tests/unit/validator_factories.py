"""Test builders for M9: the fake stack (cards, niches, gaps and a stand-in for M10)."""

import asyncio
import hashlib
import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from common.contracts.agents import ScoreRequest, ScorerRequest
from common.contracts.analyst import Gap, Niche
from common.contracts.core import DimensionScore, IdeaCard, IdeaInput, StudentProfile, TraceRecord
from common.fakes import FakeAnalyst, FakeKB
from common.http import ServiceClient
from common.llm import Cassette, LLMClient
from common.registry import load_prompts, load_rubrics
from common.settings import LlmSettings
from common.trace import TraceClient
from validators.services.effort import EffortEstimator
from validators.services.engine import Engine
from validators.settings import ValidatorSettings

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "eval/fixtures"
TODAY = date(2026, 11, 27)
IDEAS = {
    row["key"]: IdeaInput(title=row["title"], text=row["text"])
    for row in map(json.loads, (FIXTURES / "validator_ideas.jsonl").read_text().splitlines())
}
NICHES = [
    Niche(niche_id="education", name="education", size=11, trend_slope=0.08, trend_label="rising"),
    Niche(niche_id="devtools", name="devtools", size=10, trend_slope=0.01, trend_label="flat"),
]
GAPS = [
    # covered by SlideCards (lecture, slides, flashcards) / covered by no card
    Gap(gap_id="g1", niche_id="education", statement="lecture slides flashcards",
        evidence_ids=["card_3fa5305f#c0"], score=2),
    Gap(gap_id="g2", niche_id="education", statement="handwritten whiteboard equations",
        evidence_ids=["card_0c549b20#c0"], score=1),
]  # fmt: skip


def kb(cards: Iterable[IdeaCard] | None = None) -> FakeKB:
    if cards is not None:
        return FakeKB(cards)
    return FakeKB.from_jsonl(FIXTURES / "cards_small.jsonl", FIXTURES / "validator_cards.jsonl")


def profile(**changes: Any) -> StudentProfile:
    values: dict[str, Any] = {
        "user_id": "u_1",
        "skills": ["Python", "React"],
        "hours_per_week": 10,
        "weeks_available": 12,
        "team_size": 2,
        "compute": "laptop",
        "goal": "portfolio",
        "interests": ["education"],
    }
    return StudentProfile.model_validate(values | changes)


def body(idea: str | IdeaInput = "flashcards", **changes: Any) -> ScorerRequest:
    values: dict[str, Any] = {
        "run_id": "run_test",
        "idea": IDEAS[idea] if isinstance(idea, str) else idea,
        "profile": profile(),
        "evidence": [],
    }
    return ScorerRequest.model_validate(values | changes)


def reply_to(request: ScoreRequest, **changes: Any) -> dict[str, Any]:
    """A deterministic M10 answer: the same request always gets the same reply."""
    seed = hashlib.sha256(request.model_dump_json().encode()).digest()[0]
    values: dict[str, Any] = {
        "dimension": request.rubric_id.split("@")[0],
        "score": (seed % 11) / 2,
        "confidence": 0.7,
        "evidence_ids": [request.evidence[0].evidence_id],
        "features": request.features,
        "reason": "MODEL TEXT",
        "rubric_version": request.rubric_id,
        "model": "scorer-base@v1",
    }
    return DimensionScore.model_validate(values | changes).model_dump(mode="json")


class StubScorer:
    """Stands in for M10 behind the real HTTP client."""

    def __init__(self, delay_s: float = 0.0, **changes: Any) -> None:
        self.delay_s = delay_s
        self.changes = changes
        self.requests: list[ScoreRequest] = []
        self.in_flight = 0
        self.most_in_flight = 0

    async def __call__(self, http: httpx.Request) -> httpx.Response:
        request = ScoreRequest.model_validate_json(http.content)
        self.requests.append(request)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay_s)
        finally:
            self.in_flight -= 1
        return httpx.Response(200, json=reply_to(request, **self.changes))


def llm(*contents: str, tracer: TraceClient | None = None) -> LLMClient:
    cassette = Cassette([{"content": c} for c in contents])
    return LLMClient(LlmSettings(LLM_BACKEND="fake"), cassette=cassette, tracer=tracer)


def engine(
    *,
    scorer: Any = None,
    transport: httpx.AsyncBaseTransport | None = None,
    knowledge: Any = None,
    analyst: Any = "fake",
    model: LLMClient | None = None,
    records: list[TraceRecord] | None = None,
    **settings: Any,
) -> Engine:
    """``model=None`` means no effort model: the band midpoint is used."""

    async def sink(batch: list[TraceRecord]) -> None:
        if records is not None:
            records.extend(batch)

    config = ValidatorSettings(
        rubrics_dir=str(ROOT / "rubrics"), prompts_dir=str(ROOT / "prompts"), **settings
    )
    tracer = TraceClient(sink=sink)
    if model is not None:
        model._tracer = tracer
    transport = transport or httpx.MockTransport(scorer or StubScorer())
    return Engine(
        config,
        kb=knowledge if knowledge is not None else kb(),
        analyst=FakeAnalyst(NICHES, GAPS) if analyst == "fake" else analyst,
        scorer=ServiceClient("http://scorer:8000", transport=transport),
        effort=EffortEstimator(
            load_prompts(Path(config.prompts_dir))["effort"], model, config.effort_timeout_s
        ),
        rubrics=load_rubrics(Path(config.rubrics_dir))[1],
        tracer=tracer,
        today=lambda: TODAY,
    )
