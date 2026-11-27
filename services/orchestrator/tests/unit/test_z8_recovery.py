"""Partial failures (spec Z8, A4 #10-12, A7): M2 or Redis fails at every single write of a
Discover run, a challenge and a what-if apply; continuing or repeating must leave exactly one of
everything."""

import asyncio
from typing import Any

import httpx
import pytest
from orchestrator_factories import Orchestrator, Stack, error, idea, profile
from tenacity import wait_none

from common.contracts.core import StudentProfile

TWO = [idea("Diagram cards"), idea("Formula cards")]


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The HTTP client's retries stay; their pauses would add minutes to the write sweep."""
    monkeypatch.setattr("common.http.wait_exponential_jitter", lambda **_: wait_none())


CLAIM = "claim_demand_pain"


async def judged() -> tuple[Orchestrator, str]:
    m3 = Orchestrator(Stack())
    return m3, await m3.run()


async def challenge(m3: Orchestrator) -> httpx.Response:
    response = await m3.client.post(
        "/ideas/idea_1/challenge", json={"version": 1, "claim_id": CLAIM}
    )
    await m3.trace.flush()
    return response


def reject(m3: Orchestrator) -> None:
    m3.stack.check_verdicts[CLAIM] = ("unsupported", "the evidence does not say so")


# low enough for a PIVOT that one or two dimensions can lift to GO
PIVOT = dict.fromkeys(("demand_pain", "competition", "differentiation", "risk"), 3.0) | {
    "feasibility": 4.0,
    "learning": 4.0,
}


async def pivot_judged() -> tuple[Orchestrator, str]:
    stack = Stack()
    stack.scores = dict(PIVOT)
    stack.profiles["u_1"] = StudentProfile.model_validate(profile())
    m3 = Orchestrator(stack)
    return m3, await m3.run()


async def post(m3: Orchestrator, apply: str | None = None) -> httpx.Response:
    body = {"version": 1} | ({} if apply is None else {"apply": apply})
    response = await m3.client.post("/ideas/idea_1/whatif", json=body)
    if response.status_code == 202:
        await m3.wait(response.json()["run_id"])
    await m3.trace.flush()
    return response


async def listed(m3: Orchestrator) -> list[dict[str, Any]]:
    return (await post(m3)).json()["proposals"]


class Outage:
    """M2 is gone from the ``at``-th write on. ``applied``: that write happens but its reply is
    lost."""

    def __init__(self, stack: Stack, at: int, applied: bool = False) -> None:
        self.stack, self.at, self.applied = stack, at, applied
        self.writes = 0
        self.tripped = False
        stack.overrides[("profile", "/internal")] = self

    async def __call__(self, request: httpx.Request) -> httpx.Response | None:
        if request.method == "GET":
            return error(503, "DOWN") if self.tripped else None
        self.writes += 1
        if self.writes < self.at:
            return None
        if self.applied and not self.tripped:
            await self.stack._profile(request, request.url.path)
        self.tripped = True
        return error(503, "DOWN")


async def discover_writes() -> int:
    stack = Stack()
    stack.idea_rounds = [TWO]
    counter = Outage(stack, at=10_000)
    await Orchestrator(stack).discover(max_ideas=2)
    return counter.writes


@pytest.mark.parametrize("applied", [False, True], ids=["rejected", "reply_lost"])
async def test_resume_after_an_outage_at_any_write_of_a_discover_run(applied: bool) -> None:
    """Covers every step boundary, the gap between storing the child ids and creating a child,
    and the gap between create_run and a child's first checkpoint."""
    total = await discover_writes()
    assert total > 20
    problems = []
    # writes 1 and 2 happen inside POST /runs, which answers 503 itself
    for at in range(3, total + 1):
        stack = Stack()
        stack.idea_rounds = [TWO]
        outage = Outage(stack, at, applied)
        first = Orchestrator(stack)
        run_id = await first.discover(max_ideas=2)
        await first.runner.close()
        if not outage.tripped:
            continue
        stack.overrides.clear()
        second = Orchestrator(stack)
        await second.runner.recover()
        await second.wait(run_id)

        children = second.children(run_id)
        generated = [i for i in stack.ideas.values() if i.source == "generated"]
        if second.status(run_id) != "COMPLETED":
            problems.append((at, second.status(run_id), second.state(run_id).get("error_code")))
            continue
        spent = sum(stack.runs[c].spend.steps for c in children)
        own = second.state(run_id)["debate_spend"]["steps"]
        facts = (
            len(children),
            len(generated),
            len(second.verdicts(run_id)),
            len(stack.stored),
            stack.runs[run_id].spend.steps - own - spent,
        )
        if facts != (2, 2, 2, 2, 0):
            problems.append((at, facts))
    assert not problems, problems


@pytest.mark.parametrize("applied", [False, True], ids=["rejected", "reply_lost"])
@pytest.mark.parametrize("at", [1, 2, 3, 4])
async def test_a_challenge_repeated_after_a_partial_failure_makes_one_version(
    at: int, applied: bool
) -> None:
    m3, _ = await judged()
    reject(m3)
    Outage(m3.stack, at, applied)
    assert (await challenge(m3)).status_code == 503
    m3.stack.overrides.clear()
    reply = await challenge(m3)
    assert reply.status_code == 200, reply.text
    assert reply.json()["outcome"] == "removed" and reply.json()["verdict"]["version"] == 2
    assert len(m3.stack.ideas["idea_1"].versions) == 2
    assert [s.verdict.version for s in m3.stack.stored] == [1, 2]
    assert m3.status(reply.json()["verdict"]["run_id"]) == "COMPLETED"


async def applied_run_finishes(m3: Orchestrator, chosen: dict[str, Any]) -> None:
    """The student repeats the request: the run it names must be driven to its end."""
    reply = await post(m3, chosen["proposal_id"])
    assert reply.status_code == 202, reply.text
    run_id = reply.json()["run_id"]
    assert len(m3.stack.ideas["idea_1"].versions) == 2
    assert m3.status(run_id) == "COMPLETED", (
        f"apply answered 202 for run {run_id}, which stays {m3.status(run_id)} with nobody "
        "driving it"
    )


# writes of an apply before its 202: add_version, create_run, the first checkpoint
@pytest.mark.parametrize(
    ("at", "applied"), [(1, False), (1, True), (2, False), (2, True), (3, False), (3, True)]
)
async def test_an_apply_repeated_after_a_store_failure_starts_the_run(
    at: int, applied: bool
) -> None:
    m3, _ = await pivot_judged()
    chosen = (await listed(m3))[0]
    Outage(m3.stack, at, applied)
    assert (await post(m3, chosen["proposal_id"])).status_code == 503
    m3.stack.overrides.clear()
    await applied_run_finishes(m3, chosen)


async def test_an_apply_repeated_after_the_event_stream_failed_starts_the_run() -> None:
    m3, _ = await pivot_judged()
    chosen = (await listed(m3))[0]
    real = m3.stack.redis.xadd

    async def broken(*_: Any, **__: Any) -> None:
        m3.stack.redis.down = True
        try:
            m3.stack.redis._check()
        finally:
            m3.stack.redis.down = False

    m3.stack.redis.xadd = broken  # type: ignore[method-assign]
    assert (await post(m3, chosen["proposal_id"])).status_code == 503
    m3.stack.redis.xadd = real  # type: ignore[method-assign]
    await applied_run_finishes(m3, chosen)


async def test_concurrent_identical_requests_make_one_version_and_one_run() -> None:
    m3, _ = await pivot_judged()
    chosen = (await listed(m3))[0]
    replies = await asyncio.gather(*(post(m3, chosen["proposal_id"]) for _ in range(4)))
    assert {r.status_code for r in replies} == {202}
    assert len({r.json()["run_id"] for r in replies}) == 1
    assert len(m3.stack.ideas["idea_1"].versions) == 2

    reject(m3)
    runs = len(m3.stack.runs)
    replies = await asyncio.gather(*(challenge(m3) for _ in range(4)))
    assert len({r.text for r in replies}) == 1 and replies[0].status_code == 200
    assert len(m3.stack.runs) == runs + 1
    assert len(m3.stack.ideas["idea_1"].versions) == 3


async def test_apply_with_redis_down_is_503_and_adds_no_version() -> None:
    m3, _ = await pivot_judged()
    chosen = (await listed(m3))[0]
    m3.stack.redis.down = True
    assert (await post(m3, chosen["proposal_id"])).status_code == 503
    assert len(m3.stack.ideas["idea_1"].versions) == 1
