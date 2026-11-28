"""M12 claim check against recorded judge replies (spec Z6, A4 4-5; AO-7). The cassettes are
written by hand: no real model is called, so they show the pipeline, not the model's accuracy."""

from critic_factories import ROOT, CountingCassette, checker, claim, request

from common.contracts.agents import ClaimResult
from common.fakes import FakeKB
from common.llm import Cassette
from critic.services.check import UNAVAILABLE_PREFIX

CASSETTES = ROOT / "eval/behaviour/cassettes"
SLIDES = "card_816599ab#c0"  # SlideCards: lecture slides into spaced-repetition flashcards


async def judged(cassette: str, text: str) -> ClaimResult:
    replies = Cassette.load(CASSETTES / cassette)
    knowledge = FakeKB.from_jsonl(ROOT / "eval/fixtures/cards_small.jsonl")
    built = checker(cassette=replies, knowledge=knowledge)
    response = await built.check(request(claim(text, [SLIDES])), "run_1")
    return response.results[0]


async def test_supported_claim() -> None:
    result = await judged("critic_supported.jsonl", "SlideCards turns slides into flashcards")
    assert (result.verdict, result.supported) == ("supported", True)
    assert SLIDES in result.reason


async def test_contradicted_claim() -> None:
    result = await judged("critic_contradicted.jsonl", "SlideCards cannot read lecture slides")
    assert (result.verdict, result.supported) == ("unsupported", False)


async def test_model_unavailable_falls_back() -> None:
    built = checker(cassette=CountingCassette([]))
    text = "it has no offline mode"
    result = (await built.check(request(claim(text)), "run_1")).results[0]
    # every word is in the evidence, and that still does not make it supported
    assert (result.verdict, result.supported) == ("unverifiable", False)
    assert result.reason.startswith(UNAVAILABLE_PREFIX) and "100% of the claim" in result.reason


async def test_invalid_output_falls_back() -> None:
    """Prose, then a project verdict: neither is a claim verdict."""
    result = await judged("critic_invalid.jsonl", "SlideCards turns slides into flashcards")
    assert (result.verdict, result.supported) == ("unverifiable", False)
    assert result.reason.startswith(UNAVAILABLE_PREFIX) and "unusable" in result.reason
