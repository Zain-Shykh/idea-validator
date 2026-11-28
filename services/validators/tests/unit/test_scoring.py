"""Grounding, evidence bounds, concurrency, deadline and failures of the M10 call."""

import asyncio

import httpx
import pytest
from validator_factories import IDEAS, StubScorer, body, engine, kb

from common.contracts.core import Dimension, DimensionScore, EvidenceItem
from common.contracts.kb import SearchRequest, SearchResponse
from common.http import ServiceError
from validators.scorers import SCORERS
from validators.services.engine import Engine, KbUnavailableError


async def all_scores(built: Engine, idea: str = "flashcards") -> list[DimensionScore]:
    responses = [await run(built, body(idea)) for run in SCORERS.values()]
    return [score for response in responses for score in response.scores]


async def test_scores_cite_only_retrieved_ids() -> None:
    scorer = StubScorer()
    knowledge = kb()
    built = engine(scorer=scorer, knowledge=knowledge)
    scores = await all_scores(built)
    sent = {item.evidence_id for request in scorer.requests for item in request.evidence}
    assert (await knowledge.evidence(sorted(sent))).missing == []
    assert [s.score is not None for s in scores] == [True] * 6
    for score in scores:
        assert score.evidence_ids and set(score.evidence_ids) <= sent
        assert "MODEL TEXT" not in score.reason


async def test_evidence_and_text_sent_to_the_model_are_bounded() -> None:
    scorer = StubScorer()
    built = engine(scorer=scorer, max_evidence=2, max_quote_chars=60, max_idea_chars=100)
    await all_scores(built)
    assert len(scorer.requests) == 6
    for request in scorer.requests:
        assert 1 <= len(request.evidence) <= 2
        assert all(len(item.quote) <= 60 for item in request.evidence)
        assert len(request.idea.text) <= 100


async def test_only_rubric_features_are_sent() -> None:
    scorer = StubScorer()
    built = engine(scorer=scorer)
    await all_scores(built)
    for request in scorer.requests:
        rubric = built.rubrics[Dimension(request.rubric_id.split("@")[0])]
        assert set(request.features) == set(rubric.features)


async def test_ids_not_sent_are_removed() -> None:
    class Inventing(StubScorer):
        keep_real = True

        async def __call__(self, http: httpx.Request) -> httpx.Response:
            reply = (await super().__call__(http)).json()
            real = reply["evidence_ids"] if self.keep_real else []
            reply["evidence_ids"] = ["card_invented#c0", *real]
            return httpx.Response(200, json=reply)

    scorer = Inventing()
    built = engine(scorer=scorer)
    (kept,) = (await SCORERS["demand"](built, body())).scores
    assert kept.score is not None and "card_invented#c0" not in kept.evidence_ids
    scorer.keep_real = False
    (dropped,) = (await SCORERS["demand"](built, body())).scores
    assert dropped.score is None and dropped.confidence == 0 and dropped.evidence_ids == []
    assert "none of the retrieved evidence" in dropped.reason


@pytest.mark.parametrize(
    "changes",
    [
        {"dimension": "risk", "rubric_version": "risk@v1"},
        {"rubric_version": "demand_pain@v9"},
    ],
)
async def test_reply_for_another_rubric_abstains(changes: dict[str, str]) -> None:
    built = engine(scorer=StubScorer(**changes))
    (score,) = (await SCORERS["demand"](built, body())).scores
    assert score.score is None and score.dimension is Dimension.DEMAND_PAIN
    assert score.reason.startswith("scoring failed") and score.model == "none"


@pytest.mark.parametrize(
    ("model", "prefix"), [("none", "scoring failed"), ("scorer-base@v1", "the scoring model")]
)
async def test_scorer_abstention_is_passed_on_without_its_text(model: str, prefix: str) -> None:
    changes = {"score": None, "confidence": 0, "evidence_ids": [], "model": model}
    built = engine(scorer=StubScorer(**changes))
    (score,) = (await SCORERS["demand"](built, body())).scores
    assert score.score is None and score.model == model and score.reason.startswith(prefix)
    assert "MODEL TEXT" not in score.reason


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(503, json={}), "HTTP_ERROR"),
        (httpx.Response(200, json={"score": 9}), "INVALID_RESPONSE"),
        (httpx.ConnectError("down"), "UNAVAILABLE"),
        (httpx.ReadTimeout("slow"), "TIMEOUT"),
    ],
)
async def test_scorer_failure_abstains_without_retry(
    response: httpx.Response | Exception, code: str
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    built = engine(scorer=handler)
    (score,) = (await SCORERS["demand"](built, body())).scores
    assert score.reason == f"scoring failed: scorer {code}" and score.model == "none"
    assert len(calls) == 1


@pytest.mark.parametrize("limit", [1, 3])
async def test_concurrency_limit(limit: int) -> None:
    scorer = StubScorer(delay_s=0.02)
    built = engine(scorer=scorer, scorer_concurrency=limit)
    await asyncio.gather(*(run(built, body()) for run in SCORERS.values()))
    assert len(scorer.requests) == 6 and scorer.most_in_flight == limit


async def test_deadline_abstains() -> None:
    scorer = StubScorer(delay_s=0.2)
    built = engine(scorer=scorer, deadline_s=0.1)
    response = await SCORERS["competition"](built, body())
    assert [s.reason for s in response.scores] == ["scoring failed: validator deadline passed"] * 2
    assert len(scorer.requests) == 1  # the one waiting for the slot never reached M10
    # the slot was released: a later request is scored
    scorer.delay_s = 0
    assert (await SCORERS["demand"](built, body())).scores[0].score is not None


async def test_kb_failure_is_not_read_as_no_evidence() -> None:
    class Down(type(kb())):  # type: ignore[misc]
        async def search(self, request: SearchRequest) -> SearchResponse:
            raise ServiceError("TIMEOUT", "search timed out")

    for run in SCORERS.values():
        with pytest.raises(KbUnavailableError):
            await run(engine(knowledge=Down()), body())


async def test_analyst_down_leaves_its_features_empty() -> None:
    class Down:
        async def niches(self) -> None:
            raise ServiceError("UNAVAILABLE", "no analyst")

        gaps = niches

    for analyst in (Down(), None):
        built = engine(analyst=analyst)
        (demand,) = (await SCORERS["demand"](built, body())).scores
        competition = (await SCORERS["competition"](built, body())).scores
        assert demand.score is not None and demand.features["trend_growth"] is None
        assert all(s.features["gap_coverage"] is None for s in competition)


async def test_analyst_features_come_from_the_matched_niche() -> None:
    built = engine()
    (demand,) = (await SCORERS["demand"](built, body())).scores
    competition = (await SCORERS["competition"](built, body())).scores
    assert demand.features["trend_growth"] == 0.08
    assert competition[0].features["gap_coverage"] == 0.5


async def test_request_evidence_is_checked_against_the_kb() -> None:
    knowledge = kb()
    real = (await knowledge.evidence(["card_7354d76d#c0"])).items[0]
    forged = real.model_copy(update={"quote": "FORGED QUOTE"})
    unknown = EvidenceItem(
        evidence_id="card_ghost#c0", card_id="card_ghost", quote="Ghost product", source="reddit",
        url="https://example.com/g", retrieved_at=real.retrieved_at,
    )  # fmt: skip
    scorer = StubScorer()
    built = engine(scorer=scorer, knowledge=knowledge)
    response = await SCORERS["demand"](built, body(evidence=[forged, unknown]))
    quotes = {i.evidence_id: i.quote for i in scorer.requests[0].evidence}
    assert "card_ghost#c0" not in quotes
    assert all("FORGED" not in q for q in quotes.values())
    own = await SCORERS["demand"](engine(), body())
    # a devtools complaint the search did not find counts once the KB confirms it
    assert response.gate_inputs.n_sources == (own.gate_inputs.n_sources or 0) + 1


@pytest.mark.parametrize("score", [0.19, 7.5])
async def test_scores_outside_the_similarity_scale(score: float) -> None:
    """Below the minimum: dropped. Above 1: evidence, but never a competitor or duplicate."""

    class Rescored(type(kb())):  # type: ignore[misc]
        async def search(self, request: SearchRequest) -> SearchResponse:
            found = await super().search(request)
            results = [r.model_copy(update={"score": score}) for r in found.results]
            return SearchResponse(results=results, index_version=found.index_version)

    built = engine(knowledge=Rescored(kb().cards.values()))
    response = await SCORERS["competition"](built, body("slidecards_clone"))
    competition = response.scores[0]
    assert competition.features["n_competitors"] == 0
    assert competition.features["max_similarity"] is None
    assert response.gate_inputs.duplicate is None
    assert (competition.score is None) == (score < 0.2)


def test_five_fixture_ideas_exist() -> None:
    assert len(IDEAS) == 5
