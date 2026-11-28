"""A new process continues a run from its checkpoint (spec Z7, A4 #11)."""

import asyncio
from datetime import timedelta

import httpx
from orchestrator_factories import Orchestrator, Stack, error, question
from test_budget import OVER, thin_stack


async def until(condition) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(10):
        while not condition():  # noqa: ASYNC110  (polling a plain list in a test)
            await asyncio.sleep(0.005)


def hang(_: httpx.Request):  # type: ignore[no-untyped-def]
    return asyncio.sleep(3600)


async def test_restart_while_awaiting_approval() -> None:
    stack = thin_stack()
    first = Orchestrator(stack, **OVER)
    run_id = await first.run()
    assert first.status(run_id) == "AWAITING_APPROVAL"
    await first.runner.close()

    second = Orchestrator(stack, **OVER)
    await second.runner.recover()
    assert not second.runner.tasks and second.status(run_id) == "AWAITING_APPROVAL"
    response = await second.client.post(f"/runs/{run_id}/resume")
    assert response.status_code == 409  # it needs the approval, not a push
    assert (await second.approve(run_id)).status_code == 202
    assert second.status(run_id) == "COMPLETED" and stack.paths("scouts") == ["/scout"]
    seqs = [e.seq for e in second.events(run_id)]
    assert seqs == list(range(len(seqs)))


async def test_restart_while_scoring_continues_from_the_checkpoint() -> None:
    stack = Stack()
    stack.overrides[("validators", "/score/risk")] = hang
    first = Orchestrator(stack)
    run_id = await first.start()
    await until(lambda: "/score/risk" in stack.paths("validators"))
    await first.runner.close()
    assert first.status(run_id) == "RUNNING"

    stack.overrides.clear()
    stack.calls.clear()
    second = Orchestrator(stack)
    await second.runner.recover()
    await second.wait(run_id)
    assert second.status(run_id) == "COMPLETED"
    assert "intake" not in "".join(stack.paths("ideation"))  # finished steps are not redone
    assert "/search" not in stack.paths("kb")


async def test_restart_during_deep_search_does_not_buy_it_again() -> None:
    stack = thin_stack()
    stack.overrides[("scouts", "/scout")] = hang
    first = Orchestrator(stack)
    run_id = await first.start()
    await until(lambda: bool(stack.paths("scouts")))
    await first.runner.close()
    assert first.state(run_id)["deep_search"] == "started"

    second = Orchestrator(stack)
    await second.runner.recover()
    await second.wait(run_id)
    assert second.status(run_id) == "COMPLETED" and stack.paths("scouts") == ["/scout"]
    assert "deep search was interrupted" in second.state(run_id)["degraded"]
    assert stack.runs[run_id].spend.pages == 15 and not stack.cache


async def test_store_outage_leaves_the_run_resumable() -> None:
    stack = Stack()
    broken = False

    def maybe_break(request: httpx.Request) -> httpx.Response | None:
        return error(500, "INTERNAL_ERROR") if broken and request.method == "PATCH" else None

    def break_after_search(_: httpx.Request) -> None:
        nonlocal broken
        broken = True

    stack.overrides[("profile", "/internal/runs/")] = maybe_break
    stack.overrides[("kb", "/search")] = break_after_search
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "RUNNING" and not stack.stored
    assert m3.events(run_id)[-1].data == {"code": "STORE_UNAVAILABLE"}
    assert run_id in stack.redis.sets["orch:active-runs"]

    stack.overrides.clear()
    assert (await m3.post(run_id, "resume")).status_code == 202
    assert m3.status(run_id) == "COMPLETED"


async def test_resume_of_a_run_being_driven_starts_no_second_driver() -> None:
    stack = Stack()
    stack.scorer_delay_s = 0.1
    m3 = Orchestrator(stack)
    run_id = await m3.start()
    await until(lambda: bool(stack.paths("validators")))
    task = m3.runner.tasks[run_id]
    assert (await m3.client.post(f"/runs/{run_id}/resume")).status_code == 202
    assert m3.runner.tasks[run_id] is task
    await m3.wait(run_id)
    assert len(stack.scored()) == 4


async def test_two_approvals_at_once_only_one_counts() -> None:
    stack = thin_stack()
    m3 = Orchestrator(stack, **OVER)
    run_id = await m3.run()
    body = {
        "approval_id": m3.state(run_id)["pending"]["approval"]["approval_id"],
        "decision": "approve",
    }
    replies = await asyncio.gather(
        *(m3.client.post(f"/runs/{run_id}/approve", json=body) for _ in range(2))
    )
    await m3.wait(run_id)
    assert sorted(r.status_code for r in replies) == [202, 409]
    assert stack.paths("scouts") == ["/scout"]


async def test_waiting_too_long_cancels_the_run() -> None:
    stack = Stack()
    stack.questions = [question()]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    m3.runner.deps.clock.now += timedelta(days=1, seconds=1)
    response = await m3.answer(run_id)
    assert response.status_code == 409 and m3.status(run_id) == "CANCELLED"
    assert not stack.redis.sets.get("orch:active-runs")
    assert len(stack.ideas["idea_1"].versions) == 1
