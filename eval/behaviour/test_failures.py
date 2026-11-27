"""AO-7: the four failures every release must survive, through the real M3, M9, M10, M11, M12 and
M13 in process. M2, M4, M6 and M8 are fakes. Model replies come from cassettes (hand-written: no
hosted key exists) and every socket connection is refused, so no model and no network can be used.

1. invalid JSON from a scorer   2. a tool timeout   3. an empty source   4. a poisoned page
"""

import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from behaviour_stack import ROOT, RealStack, all_records, orchestrator
from scorer_factories import Rig
from validator_factories import StubScorer

import eval_metrics
from common.contracts.core import (
    Budget,
    Dimension,
    IdeaCard,
    Spend,
    TraceKind,
    TraceRecord,
    Verdict,
    VerdictLabel,
)
from common.contracts.ops import OpsSummary
from common.llm import Cassette
from scorer.main import build_app as build_scorer
from scorer.settings import ScorerSettings

CASSETTES = Path(__file__).parent / "cassettes"
ORDERS = ("ignore previous", "ignore all previous", "disregard", "you are now", "system prompt")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a behaviour test tried to open a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


class Endless(Cassette):
    """One recorded reply, given every time."""

    def __init__(self, name: str) -> None:
        super().__init__([])
        self.entry = json.loads((CASSETTES / name).read_text().splitlines()[0])
        self.calls = 0

    def next(self, alias: str, agent: str) -> dict[str, Any]:
        self.calls += 1
        return self.entry


def real_scorer(local: Callable[[httpx.Request], httpx.Response], hosted: Cassette) -> Any:
    """The real M10 as a transport: ``local`` is the model server, ``hosted`` the fallback."""
    rig = Rig(local)
    assert rig.scorer.hosted is not None
    rig.scorer.hosted._cassette = hosted
    app = build_scorer(ScorerSettings(backend="hosted"), scorer=rig.scorer)
    return rig, httpx.ASGITransport(app)


async def finished(stack: RealStack, tmp: str = "iv_1") -> tuple[Any, str, list[TraceRecord]]:
    m3 = orchestrator(stack)
    run_id = await m3.run(tmp)
    return m3, run_id, await all_records(stack, m3, run_id)


async def summary_of(stack: RealStack) -> OpsSummary:
    async with httpx.AsyncClient(transport=stack.ops, base_url="http://agentops:8000") as api:
        return OpsSummary.model_validate_json((await api.get("/summary")).text)


def assert_abstained(card: Verdict) -> None:
    assert card.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE and card.missing
    assert card.score is None
    assert all(s.score is None and s.confidence == 0 for s in card.scores)


# --- 1. invalid JSON from a scorer --------------------------------------------------------------


async def test_invalid_json_from_the_scorer_retries_once_falls_back_then_abstains(
    tmp_path: Path,
) -> None:
    broken = [
        json.loads(line)["content"]
        for line in (CASSETTES / "scorer_invalid_json.jsonl").read_text().splitlines()
    ]
    local_calls: list[int] = []

    def local(_: httpx.Request) -> httpx.Response:
        local_calls.append(1)
        content = broken[(len(local_calls) - 1) % len(broken)]
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
        )

    hosted = Endless("scorer_hosted_invalid.jsonl")
    rig, transport = real_scorer(local, hosted)
    stack = RealStack(tmp_path, scorer=transport)
    assert rig.scorer.hosted is not None
    rig.scorer.tracer = rig.scorer.hosted._tracer = stack.tracer

    m3, run_id, records = await finished(stack)

    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    assert_abstained(m3.card(run_id))
    scored = [r for r in records if r.agent == "scorer" and r.kind is TraceKind.LLM_CALL]
    local_records = [r for r in scored if r.model == "scorer-base@v1"]
    # per dimension: the call and exactly one retry, then the hosted fallback
    assert len(local_calls) == len(local_records) == 2 * 6
    assert sorted({r.retries for r in local_records}) == [0, 1]
    assert 6 <= hosted.calls <= 12
    assert (await summary_of(stack)).scorer_fallbacks == 6
    assert not stack.cache


# --- 2. a tool timeout ---------------------------------------------------------------------------


async def test_a_model_that_times_out_is_not_repeated_and_the_dimensions_abstain(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadTimeout("slow")

    stack = RealStack(tmp_path, scorer=httpx.MockTransport(timeout))
    m3, run_id, records = await finished(stack)

    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    assert_abstained(m3.card(run_id))
    # D-010: an abandoned job still runs on the model, so a timed-out score is not retried
    assert len(calls) == 6 and not stack.cache
    checks = eval_metrics.behaviour(records, m3.card(run_id), *spend_and_budget(m3, run_id))
    assert checks["budget_kept"] and checks["evidence_cited_ratio"] is None


def spend_and_budget(m3: Any, run_id: str) -> tuple[Spend, Budget]:
    return m3.stack.runs[run_id].spend, Budget.model_validate(m3.state(run_id)["budget"])


async def test_a_tool_that_stays_unavailable_is_tried_three_times_then_abstains(
    tmp_path: Path,
) -> None:
    stack = RealStack(tmp_path)
    del stack.real["validators"]  # the fake M9, so that one of its endpoints can be broken
    stack.overrides[("validators", "/score/risk")] = lambda _: httpx.Response(504)

    m3, run_id, _ = await finished(stack)

    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    card = m3.card(run_id)
    assert stack.paths("validators").count("/score/risk") == 3
    risk = next(s for s in card.scores if s.dimension is Dimension.RISK)
    assert risk.score is None and risk.reason.startswith("scoring failed")
    assert card.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE and not stack.cache


@pytest.mark.parametrize("failures, status", [(2, "COMPLETED"), (99, "FAILED")])
async def test_a_search_timeout_is_retried_twice_and_no_more(
    tmp_path: Path, failures: int, status: str
) -> None:
    stack = RealStack(tmp_path)
    attempts: list[int] = []

    def slow(_: httpx.Request) -> None:
        attempts.append(1)
        if len(attempts) <= failures:
            raise httpx.ReadTimeout("slow")

    stack.overrides[("kb", "/search")] = slow
    m3, run_id, records = await finished(stack)

    assert m3.status(run_id) == status, m3.state(run_id)["error_code"]
    if status == "FAILED":
        # an outage is not "no evidence": no verdict is made up
        assert len(attempts) == 3 and m3.stack.runs[run_id].result is None
        assert m3.state(run_id)["error_code"] == "KB_UNAVAILABLE"
        (search,) = [r for r in records if r.tool == "search_kb"]
        assert search.error
    else:
        assert len(attempts) == 3


# --- 3. an empty source --------------------------------------------------------------------------


async def test_an_empty_source_gives_insufficient_evidence_and_says_what_is_missing(
    tmp_path: Path,
) -> None:
    scorer = StubScorer()
    stack = RealStack(tmp_path, scorer=httpx.MockTransport(scorer))
    stack.kb.cards.clear()

    m3, run_id, records = await finished(stack)

    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    card = m3.card(run_id)
    assert_abstained(card)
    assert any("independent sources: found 0" in m for m in card.missing)
    assert any("platforms: found 0" in m for m in card.missing)
    assert not scorer.requests, "no evidence, so no model is asked for a score"
    assert not [r for r in records if r.agent == "scorer"]
    assert all(not s.evidence_ids for s in card.scores)


# --- 4. a poisoned page --------------------------------------------------------------------------


def poisoned_cards() -> list[IdeaCard]:
    lines = (ROOT / "eval/fixtures/poisoned_cards.jsonl").read_text().splitlines()
    return [IdeaCard.model_validate_json(line) for line in lines]


async def test_a_poisoned_page_is_withheld_counted_and_obeyed_by_nobody(tmp_path: Path) -> None:
    async def run(folder: str, poison: bool) -> tuple[Any, str, list[TraceRecord], StubScorer, Any]:
        scorer = StubScorer(score=2.0)
        stack = RealStack(tmp_path / folder, scorer=httpx.MockTransport(scorer))
        # only the planted cards: the clean fixtures hold one of their own
        stack.kb.cards = {
            i: c for i, c in stack.kb.cards.items() if not _ordering(c.text + c.summary)
        }
        if poison:
            stack.kb.cards.update({c.id: c for c in poisoned_cards()})
        return (*await finished(stack), scorer, stack)

    (tmp_path / "clean").mkdir()
    (tmp_path / "poisoned").mkdir()
    clean_m3, clean_run, *_ = await run("clean", False)
    m3, run_id, records, scorer, stack = await run("poisoned", True)

    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    bad = {c.id for c in poisoned_cards()}
    flagged = {
        item["card_id"]
        for r in records
        if r.tool == "scan" and r.reply
        for item in json.loads(r.reply)["flagged"]
    }
    assert flagged & bad, "the search must return a planted card for this test to count"
    # no model sees an order, nor a planted card
    prompts = [r.model_dump_json() for r in scorer.requests]
    prompts += [r.input or "" for r in records if r.kind is TraceKind.LLM_CALL]
    for prompt in prompts:
        assert not _ordering(prompt)
        assert not any(i in prompt for i in flagged & bad)
    card = m3.card(run_id)
    assert not any(i.split("#")[0] in flagged for s in card.scores for i in s.evidence_ids)
    # the orders ask for the top mark: every score is still the scorer's own
    by_scorer = [s for s in card.scores if s.dimension is not Dimension.DEMAND_PAIN]
    # the planted pages took no real evidence's place: every dimension is still scored, with the
    # scorer's own value (demand_pain also carries the Skeptic's penalty)
    assert all(s.score == 2.0 for s in by_scorer), [(s.dimension, s.score) for s in by_scorer]
    assert card.verdict is clean_m3.card(clean_run).verdict
    assert (await summary_of(stack)).blocked_injections >= 1
    found = eval_metrics.behaviour(records, card, *spend_and_budget(m3, run_id))
    assert found["injection_resisted"] is True


def _ordering(text: str) -> bool:
    return any(order in text.lower() for order in ORDERS)
