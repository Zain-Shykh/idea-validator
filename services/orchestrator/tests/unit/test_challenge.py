"""Challenge a claim (spec Z8, A4 #11, #13, #16) on the fake stack with the real M11."""

from typing import Any

import httpx
import pytest
from orchestrator_factories import Orchestrator, Stack, error

from common.contracts.agents import CheckRequest
from common.contracts.core import Dimension, Verdict, VerdictRequest
from orchestrator.services import revise

CLAIM = "claim_demand_pain"


async def judged(**fields: Any) -> tuple[Orchestrator, str]:
    stack = Stack()
    for name, value in fields.items():
        setattr(stack, name, value)
    m3 = Orchestrator(stack)
    return m3, await m3.run()


async def challenge(
    m3: Orchestrator, claim: str = CLAIM, version: int = 1, idea: str = "idea_1", **kwargs: Any
) -> httpx.Response:
    response = await m3.client.post(
        f"/ideas/{idea}/challenge", json={"version": version, "claim_id": claim}, **kwargs
    )
    await m3.trace.flush()
    return response


def reject(m3: Orchestrator, claim: str = CLAIM, why: str = "the evidence does not say so") -> None:
    m3.stack.check_verdicts[claim] = ("unsupported", why)


async def test_a_supported_claim_is_defended_and_nothing_changes() -> None:
    m3, run_id = await judged()
    before = m3.card(run_id)
    stored, versions, runs = (
        len(m3.stack.stored),
        len(m3.stack.ideas["idea_1"].versions),
        len(m3.stack.runs),
    )
    reply = (await challenge(m3)).json()
    assert (reply["outcome"], reply["rationale"]) == ("defended", "ok")
    assert Verdict.model_validate(reply["verdict"]) == before
    assert len(m3.stack.stored) == stored and len(m3.stack.runs) == runs
    assert len(m3.stack.ideas["idea_1"].versions) == versions
    sent = m3.stack.sent("critic/check", CheckRequest)[-1]
    assert sent.strict and [c.claim_id for c in sent.claims] == [CLAIM]


async def test_the_claim_is_sent_without_the_codes_bookkeeping() -> None:
    reason = "demand_pain 4/5 from scorer-base@v1. 3 complaints on 2 platforms. Cites a#c0, b#c1."
    assert revise.claim_text(reason) == "3 complaints on 2 platforms."
    assert revise.claim_text("plain words") == "plain words"
    assert revise.claim_text("Cites a#c0.") == "Cites a#c0."


async def test_a_removed_claim_makes_a_new_version_with_m11s_verdict() -> None:
    m3, run_id = await judged()
    before = m3.card(run_id)
    reject(m3)
    reply = (await challenge(m3)).json()
    assert (reply["outcome"], reply["rationale"]) == ("removed", "the evidence does not say so")
    card = Verdict.model_validate(reply["verdict"])
    assert (card.version, card.idea_id) == (2, before.idea_id) and card.run_id != run_id
    demand = next(s for s in card.scores if s.dimension is Dimension.DEMAND_PAIN)
    assert demand.score is None and demand.evidence_ids == []
    version = m3.stack.ideas["idea_1"].versions[-1]
    assert (version.origin, version.version, version.text) == (
        "challenge", 2, m3.stack.ideas["idea_1"].versions[0].text,
    )  # fmt: skip
    assert m3.status(card.run_id) == "COMPLETED"
    assert m3.stack.stored[-1].idea_version_id == version.id
    assert CLAIM not in [c.claim_id for c in m3.stack.stored[-1].claims]
    # the engine's input is the only thing that changed
    sent = m3.stack.sent("verdict/verdict", VerdictRequest)[-1]
    first = m3.stack.sent("verdict/verdict", VerdictRequest)[-2]
    assert sent.gate_inputs == first.gate_inputs
    assert [s for s in sent.scores if s.dimension is not Dimension.DEMAND_PAIN] == [
        s for s in first.scores if s.dimension is not Dimension.DEMAND_PAIN
    ]
    # the first version keeps its verdict
    assert m3.card(run_id) == before


async def test_stored_verdict_is_m11s_reply() -> None:
    m3, _ = await judged()
    reject(m3)
    await challenge(m3)
    sent = m3.stack.sent("verdict/verdict", VerdictRequest)[-1]
    transport = httpx.ASGITransport(app=__import__("verdict.main").main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://verdict") as engine:
        again = await engine.post(
            "/verdict",
            content=sent.model_dump_json(by_alias=True),
            headers={"content-type": "application/json"},
        )
    assert m3.stack.stored[-1].verdict == Verdict.model_validate_json(again.text)


async def test_the_new_version_can_be_challenged_again() -> None:
    m3, _ = await judged()
    reject(m3)
    await challenge(m3)
    reject(m3, "claim_risk")
    reply = await challenge(m3, "claim_risk", version=2)
    card = Verdict.model_validate(reply.json()["verdict"])
    assert card.version == 3
    assert [s.dimension for s in card.scores if s.score is None] == ["demand_pain", "risk"]


async def test_a_repeated_challenge_returns_the_same_result() -> None:
    m3, _ = await judged()
    reject(m3)
    first = (await challenge(m3)).json()
    checks = len(m3.stack.paths("critic"))
    assert (await challenge(m3)).json() == first
    assert len(m3.stack.paths("critic")) == checks
    assert len(m3.stack.ideas["idea_1"].versions) == 2


@pytest.mark.parametrize(
    "answer",
    [
        ("unverifiable", "claim check unavailable: the model timed out"),
        None,  # M12 is down
    ],
)
async def test_a_check_that_could_not_run_changes_nothing(answer: tuple[str, str] | None) -> None:
    m3, _ = await judged()
    if answer is None:
        m3.stack.overrides[("critic", "/check")] = lambda _: error(503, "NOT_READY")
    else:
        m3.stack.check_verdicts[CLAIM] = answer
    stored = len(m3.stack.stored)
    response = await challenge(m3)
    assert response.status_code == 503
    assert len(m3.stack.stored) == stored and len(m3.stack.ideas["idea_1"].versions) == 1


async def test_a_judges_unverifiable_removes_the_claim() -> None:
    m3, _ = await judged()
    m3.stack.check_verdicts[CLAIM] = ("unverifiable", "the cited evidence is not enough")
    assert (await challenge(m3)).json()["outcome"] == "removed"


async def test_a_cached_verdict_is_challenged_with_the_source_runs_input() -> None:
    m3, first = await judged()
    second = await m3.run()
    assert m3.card(second).from_cache and m3.state(second)["source_run_id"] == first
    reject(m3)
    reply = await challenge(m3)
    assert reply.status_code == 200, reply.text
    assert reply.json()["verdict"]["version"] == 2


async def test_a_discover_idea_can_be_challenged() -> None:
    m3 = Orchestrator(Stack())
    await m3.discover(max_ideas=1)
    reject(m3)
    assert (await challenge(m3, idea="idea_gen1")).json()["outcome"] == "removed"


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"idea": "idea_nope"}, 404),
        ({"version": 7}, 404),
        ({"claim": "claim_nope"}, 404),
        ({"headers": {"X-User-Id": "u_2"}}, 404),
    ],
)
async def test_ownership(kwargs: dict[str, Any], status: int) -> None:
    m3, _ = await judged()
    reject(m3)
    assert (await challenge(m3, **kwargs)).status_code == status
    assert len(m3.stack.ideas["idea_1"].versions) == 1


async def test_a_user_header_is_required() -> None:
    m3, _ = await judged()
    m3.client.headers.pop("X-User-Id")
    assert (await challenge(m3)).status_code == 422


async def test_an_abstained_dimension_has_no_claim() -> None:
    m3, _ = await judged()
    reject(m3)
    await challenge(m3)
    assert (await challenge(m3, version=2)).status_code == 404


async def test_a_run_without_the_engine_input_is_409() -> None:
    m3, run_id = await judged()
    record = m3.stack.runs[run_id]
    m3.stack.runs[run_id] = record.model_copy(
        update={"state": record.state | {"verdict_request": None}}
    )
    assert (await challenge(m3)).status_code == 409


@pytest.mark.parametrize(
    ("host", "path"),
    [("profile", "/internal/ideas"), ("profile", "/internal/runs"), ("verdict", "/verdict")],
)
async def test_a_dependency_down_is_503(host: str, path: str) -> None:
    m3, _ = await judged()
    reject(m3)
    m3.stack.overrides[(host, path)] = lambda _: error(503, "DOWN")
    assert (await challenge(m3)).status_code == 503
    m3.stack.overrides.clear()
    # a repeat after the outage completes it
    assert (await challenge(m3)).json()["outcome"] == "removed"
