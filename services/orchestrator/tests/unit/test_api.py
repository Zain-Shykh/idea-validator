"""The M3 API: state conflicts, ownership and failures (spec Z7, A4 #1, A5)."""

import pytest
from orchestrator_factories import (
    HEADERS,
    USER,
    Orchestrator,
    Stack,
    error,
    profile,
    question,
)
from test_budget import OVER, thin_stack

from common.contracts.core import ErrorEnvelope

START = {"workflow": "validate", "user_id": USER, "idea_version_id": "iv_1"}
APPROVE = {"approval_id": "appr_x", "decision": "approve"}
ANSWER = {"answers": [{"question_id": "q1", "answer": "x"}]}
ACTIONS = [("approve", APPROVE), ("answer", ANSWER), ("resume", None)]


def code(response) -> tuple[int, str]:  # type: ignore[no-untyped-def]
    return response.status_code, ErrorEnvelope.model_validate_json(response.text).error.code


async def test_wrong_state_is_409() -> None:
    m3 = Orchestrator(Stack())
    done = await m3.run()
    for action, body in ACTIONS:
        assert code(await m3.post(done, action, body)) == (409, "CONFLICT"), action

    waiting_stack = thin_stack()
    other = Orchestrator(waiting_stack, **OVER)
    waiting = await other.run()
    assert code(await other.post(waiting, "answer", ANSWER)) == (409, "CONFLICT")
    assert code(await other.post(waiting, "approve", APPROVE)) == (409, "CONFLICT")  # wrong id
    assert other.status(waiting) == "AWAITING_APPROVAL"

    asking = Stack()
    asking.questions = [question()]
    third = Orchestrator(asking)
    run_id = await third.run()
    assert code(await third.post(run_id, "approve", APPROVE)) == (409, "CONFLICT")


async def test_another_user_sees_no_run() -> None:
    stack = thin_stack()
    m3 = Orchestrator(stack, **OVER)
    run_id = await m3.run()
    stranger = {"X-User-Id": "u_2"}
    assert code(await m3.client.get(f"/runs/{run_id}", headers=stranger)) == (404, "NOT_FOUND")
    for action, body in ACTIONS:
        response = await m3.client.post(f"/runs/{run_id}/{action}", json=body, headers=stranger)
        assert code(response) == (404, "NOT_FOUND"), action
    assert m3.status(run_id) == "AWAITING_APPROVAL"
    m3.client.headers.pop("X-User-Id")
    assert (await m3.client.get(f"/runs/{run_id}")).status_code == 422
    assert (await m3.client.get(f"/runs/{run_id}", headers=HEADERS)).status_code == 200


async def test_unknown_run_and_bad_ids() -> None:
    m3 = Orchestrator(Stack())
    assert code(await m3.client.get("/runs/run_missing")) == (404, "NOT_FOUND")
    for action, body in ACTIONS:
        response = await m3.client.post(f"/runs/run_missing/{action}", json=body)
        assert code(response) == (404, "NOT_FOUND")
    assert (await m3.client.get("/runs/a%3Fb")).status_code == 422


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"workflow": "discover"}, (501, "NOT_IMPLEMENTED")),
        ({"idea_version_id": "iv_missing"}, (404, "NOT_FOUND")),
        ({"idea_version_id": "iv_theirs"}, (404, "NOT_FOUND")),
        ({"profile": profile(user_id="u_2")}, (422, "VALIDATION_ERROR")),
    ],
)
async def test_start_is_refused(change: dict, expected: tuple[int, str]) -> None:
    stack = Stack()
    stack.add_idea("iv_theirs", user="u_2")
    m3 = Orchestrator(stack)
    response = await m3.client.post("/runs", json=START | {"profile": profile()} | change)
    assert code(response) == expected
    assert not stack.runs


async def test_start_for_someone_else_is_403() -> None:
    m3 = Orchestrator(Stack())
    response = await m3.client.post(
        "/runs", json=START | {"profile": profile()}, headers={"X-User-Id": "u_2"}
    )
    assert code(response) == (403, "FORBIDDEN")


async def test_start_without_a_user_header_is_refused() -> None:
    """Fails closed: a gateway that forgets the header starts nothing for anyone."""
    m3 = Orchestrator(Stack())
    m3.client.headers.pop("X-User-Id")
    response = await m3.client.post("/runs", json=START | {"profile": profile()})
    assert code(response) == (422, "VALIDATION_ERROR")
    assert not m3.stack.runs and not m3.stack.calls


async def test_store_down_is_503() -> None:
    stack = Stack()
    stack.overrides[("profile", "/internal")] = lambda _: error(500, "INTERNAL_ERROR")
    m3 = Orchestrator(stack)
    response = await m3.client.post("/runs", json=START | {"profile": profile()})
    assert code(response) == (503, "DEPENDENCY_UNAVAILABLE")


async def test_redis_down_gives_no_run_id() -> None:
    stack = Stack()
    stack.redis.down = True
    m3 = Orchestrator(stack)
    response = await m3.client.post("/runs", json=START | {"profile": profile()})
    assert code(response) == (503, "DEPENDENCY_UNAVAILABLE")
    assert not m3.runner.tasks and not stack.paths("validators")


async def test_redis_failing_mid_run_does_not_stop_it() -> None:
    stack = Stack()

    def break_redis(_: object) -> None:
        stack.redis.down = True

    stack.overrides[("kb", "/search")] = break_redis
    m3 = Orchestrator(stack)
    assert m3.status(await m3.run()) == "COMPLETED"


@pytest.mark.parametrize(
    ("action", "body"),
    [("challenge", {"version": 1, "claim_id": "c1"}), ("whatif", {"version": 1})],
)
async def test_challenge_and_whatif_are_not_here_yet(action: str, body: dict) -> None:
    """The Z1 stub reported a claim as removed without checking it."""
    m3 = Orchestrator(Stack())
    response = await m3.client.post(f"/ideas/idea_1/{action}", json=body)
    assert code(response) == (501, "NOT_IMPLEMENTED")
    assert not m3.stack.calls
