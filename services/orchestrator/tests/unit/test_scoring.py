"""Scorer calls: one 600 s limit, bounded retries, abstain (M3-3, D-010; spec Z7, A4 #10)."""

import asyncio

import httpx
import pytest
from orchestrator_factories import Orchestrator, Stack, error

from common.contracts.core import VerdictLabel
from common.http import VALIDATOR_TIMEOUT
from orchestrator.settings import OrchestratorSettings


def failing(answer: httpx.Response | Exception):  # type: ignore[no-untyped-def]
    def override(_: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    return override


def test_the_default_limit_is_the_validator_timeout() -> None:
    assert OrchestratorSettings().scorer_timeout_s == VALIDATOR_TIMEOUT.read == 600


@pytest.mark.parametrize(
    ("answer", "attempts", "code"),
    [
        (httpx.ReadTimeout("slow"), 1, "TIMEOUT"),
        (httpx.Response(200, content=b"not json"), 3, "INVALID_RESPONSE"),
        (httpx.Response(200, json={"scores": []}), 3, "INVALID_RESPONSE"),
        (error(500, "INTERNAL_ERROR"), 3, "INTERNAL_ERROR"),
        (error(429, "RATE_LIMITED"), 3, "RATE_LIMITED"),
        (error(422, "VALIDATION_ERROR"), 1, "VALIDATION_ERROR"),
        (httpx.ConnectError("refused"), 3, "UNAVAILABLE"),
    ],
)
async def test_failing_scorer_abstains_and_the_run_completes(
    answer: httpx.Response | Exception, attempts: int, code: str
) -> None:
    stack = Stack()
    stack.overrides[("validators", "/score/competition")] = failing(answer)
    m3 = Orchestrator(stack)
    run_id = await m3.run()

    assert m3.status(run_id) == "COMPLETED"
    assert stack.paths("validators").count("/score/competition") == attempts
    card = m3.card(run_id)
    lost = [s for s in card.scores if s.dimension in ("competition", "differentiation")]
    assert [(s.score, s.confidence, s.model) for s in lost] == [(None, 0, "none")] * 2
    assert {s.reason for s in lost} == {f"scoring failed: validator {code}"}
    assert card.verdict is not VerdictLabel.GO
    (record,) = [r for r in stack.records if r.tool == "score_competition"]
    assert record.retries == attempts - 1 and record.error == f"scorer failed: {code}"


async def test_one_limit_covers_all_attempts() -> None:
    stack = Stack()

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return error(500, "INTERNAL_ERROR")

    stack.overrides[("validators", "/score/risk")] = slow
    m3 = Orchestrator(stack, scorer_timeout_s=0.3)
    run_id = await m3.run()
    assert m3.status(run_id) == "COMPLETED"
    assert stack.paths("validators").count("/score/risk") == 2  # the third never starts
    risk = next(s for s in m3.card(run_id).scores if s.dimension == "risk")
    assert risk.reason == "scoring failed: validator TIMEOUT"


async def test_one_good_retry_is_enough() -> None:
    stack = Stack()
    answers = [error(500, "INTERNAL_ERROR")]
    stack.overrides[("validators", "/score/risk")] = lambda _: answers.pop() if answers else None
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.card(run_id).verdict is VerdictLabel.GO and stack.cache


async def test_kb_outage_in_a_scorer_fails_the_run() -> None:
    stack = Stack()
    stack.overrides[("validators", "/score/demand")] = lambda _: error(503, "KB_UNAVAILABLE")
    stack.scorer_delay_s = 0.2
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "FAILED" and m3.state(run_id)["error_code"] == "KB_UNAVAILABLE"
    assert stack.in_flight == 0  # the other scorers were cancelled
    assert not stack.stored


async def test_unknown_evidence_ids_are_stripped() -> None:
    stack = Stack()
    original = stack.scorer_response

    def invented(name, body):  # type: ignore[no-untyped-def]
        reply = original(name, body)
        if name != "risk":
            return reply
        scores = [s.model_copy(update={"evidence_ids": ["card_deadbeef#c0"]}) for s in reply.scores]
        return reply.model_copy(update={"scores": scores})

    stack.scorer_response = invented  # type: ignore[method-assign]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    risk = next(s for s in m3.card(run_id).scores if s.dimension == "risk")
    assert risk.score is None and risk.evidence_ids == [] and not stack.cache


@pytest.mark.parametrize(
    ("host", "path", "code"),
    [
        ("kb", "/search", "KB_UNAVAILABLE"),
        ("kb", "/evidence", "KB_UNAVAILABLE"),
        ("critic", "/rail", "NOT_READY"),
        ("verdict", "/config", "NOT_READY"),
        ("verdict", "/verdict", "NOT_READY"),
        ("verdict", "/whatif", "NOT_READY"),
    ],
)
async def test_essential_dependency_down_fails_the_run(host: str, path: str, code: str) -> None:
    stack = Stack()
    stack.overrides[(host, path)] = lambda _: error(503, "NOT_READY")
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "FAILED" and m3.state(run_id)["error_code"] == code
    assert stack.paths(host).count(next(p for p in stack.paths(host) if p.startswith(path))) <= 3
    assert not stack.stored and m3.events(run_id)[-1].message == "Run failed"


async def test_refused_idea_fails_before_any_model_sees_it() -> None:
    stack = Stack()
    stack.add_idea("iv_bad", text="REJECT me")
    m3 = Orchestrator(stack)
    run_id = await m3.run("iv_bad")
    assert m3.status(run_id) == "FAILED" and m3.state(run_id)["error_code"] == "INPUT_REJECTED"
    assert not stack.paths("ideation") and not stack.paths("validators")


async def test_unexpected_error_fails_the_run_without_leaking_it() -> None:
    stack = Stack()

    def boom(_: httpx.Request) -> httpx.Response:
        raise RuntimeError("secret detail")

    stack.overrides[("kb", "/search")] = boom
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "FAILED" and m3.state(run_id)["error_code"] == "INTERNAL_ERROR"
    assert "secret detail" not in "".join(e.model_dump_json() for e in m3.events(run_id))
