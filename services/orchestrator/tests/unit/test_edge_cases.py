"""Races, partial failures, bypasses and the A7 rows nothing else covers."""

import asyncio
import json
import logging
from datetime import timedelta

import httpx
import pytest
from orchestrator_factories import NOW, Orchestrator, Stack, error, profile, question
from test_budget import OVER, thin_stack
from test_resume import until
from test_verdict_guard import pivot_stack

from common.contracts.agents import ScorerRequest
from common.contracts.core import RunEventType, TraceKind, TraceRecord
from common.contracts.scouts import ScoutRequest
from common.service import JsonFormatter


def fail_patch_once(stack: Stack, status: str) -> None:
    """M2 loses the checkpoint that carries ``status``, once (all of the client's attempts)."""
    armed = {"left": 3}

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.method != "PATCH" or not armed["left"]:
            return None
        if json.loads(request.content).get("status") != status:
            return None
        armed["left"] -= 1
        return error(500, "INTERNAL_ERROR")

    stack.overrides[("profile", "/internal/runs/")] = override


def spent(tokens: int) -> TraceRecord:
    return TraceRecord(
        run_id="r", step_id=1, agent="ideation", kind=TraceKind.LLM_CALL, tokens_in=tokens, ts=NOW
    )


# --- AVOID-2: nothing degraded may be reused -----------------------------------------------------


async def test_stripped_scorer_reply_is_not_memoised_and_never_reaches_the_cache() -> None:
    """Run 1 strips an unknown id (degraded). Run 2 must not inherit that reply silently."""
    stack = Stack()
    original = stack.scorer_response

    def one_invented_id(name: str, body: ScorerRequest):  # type: ignore[no-untyped-def]
        reply = original(name, body)
        if name != "risk":
            return reply
        scores = [
            s.model_copy(update={"evidence_ids": [*s.evidence_ids, "card_deadbeef#c0"]})
            for s in reply.scores
        ]
        return reply.model_copy(update={"scores": scores})

    stack.scorer_response = one_invented_id  # type: ignore[method-assign]
    m3 = Orchestrator(stack)
    first = await m3.run()
    assert m3.state(first)["degraded"] and not stack.cache

    second = await m3.run()
    assert m3.status(second) == "COMPLETED"
    assert not stack.cache, "the stripped reply came back from the memo and the card was cached"


# --- AO-2 / AVOID-1 ------------------------------------------------------------------------------


async def test_deep_search_does_not_start_with_the_token_cap_used_up() -> None:
    stack = thin_stack()

    def burn(_: httpx.Request) -> None:
        stack.agent_records = [spent(10_000_000)]

    stack.overrides[("kb", "/search")] = burn
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert not stack.paths("scouts"), "a paid step started although max_tokens is used up"
    assert m3.status(run_id) == "BUDGET_STOPPED"


async def test_approved_page_count_is_what_gets_bought_after_a_restart() -> None:
    """U4: the student approved 25 pages; a config change must not buy 100."""
    stack = thin_stack()
    first = Orchestrator(stack, **OVER)
    run_id = await first.run()
    await first.runner.close()

    second = Orchestrator(stack, deep_search_pages=100)
    assert (await second.approve(run_id)).status_code == 202
    (scout,) = stack.sent("scouts/scout", ScoutRequest)
    assert scout.page_budget == 25


# --- concurrency ---------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "resume"])
async def test_startup_recovery_racing_a_request_starts_one_driver(action: str) -> None:
    """Both look at ``_busy`` / spawn around an await; recovery takes no lock."""
    stack = thin_stack()
    stack.overrides[("scouts", "/scout")] = lambda _: asyncio.sleep(0.05)
    m3 = Orchestrator(stack, **OVER)
    if action == "approve":
        run_id = await m3.run()
    else:  # a run left RUNNING by a stopped process
        stack = Stack()
        stack.scorer_delay_s = 0.05
        before = Orchestrator(stack)
        run_id = await before.start()
        await until(lambda: bool(stack.paths("validators")))
        await before.runner.close()
        stack.calls.clear()
        m3 = Orchestrator(stack)

    gate, reached = asyncio.Event(), asyncio.Event()
    add = m3.runner.deps.active.add

    async def slow_add(rid: str) -> None:  # a slow Redis SADD in the request
        await add(rid)
        reached.set()
        await gate.wait()

    m3.runner.deps.active.add = slow_add  # type: ignore[method-assign]
    body = None
    if action == "approve":
        approval_id = m3.state(run_id)["pending"]["approval"]["approval_id"]
        body = {"approval_id": approval_id, "decision": "approve"}
    request = asyncio.create_task(m3.client.post(f"/runs/{run_id}/{action}", json=body))
    await reached.wait()

    spawned: list[str] = []
    spawn = m3.runner._spawn

    def counting_spawn(rid: str) -> None:
        spawned.append(rid)
        spawn(rid)

    m3.runner._spawn = counting_spawn  # type: ignore[method-assign]
    await m3.runner.recover()
    gate.set()
    assert (await request).status_code == 202
    await m3.wait(run_id)
    await asyncio.sleep(0.3)  # let an orphaned second driver show itself
    assert len(spawned) == 1, f"{len(spawned)} drivers were started for one run"


async def test_event_sequence_is_consecutive_when_redis_yields() -> None:
    """The in-memory Redis never suspends, so alone it cannot show the per-run event lock."""
    stack = Stack()
    read = stack.redis.xrevrange

    async def yielding(key: str, count: int):  # type: ignore[no-untyped-def]
        found = await read(key, count)
        await asyncio.sleep(0.001)
        return found

    stack.redis.xrevrange = yielding  # type: ignore[method-assign]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    seqs = [e.seq for e in m3.events(run_id)]
    assert seqs == list(range(len(seqs)))


@pytest.mark.parametrize("outcome", ["COMPLETED", "FAILED", "BUDGET_STOPPED"])
async def test_a_finished_run_leaves_no_task_or_lock_behind(outcome: str) -> None:
    stack = Stack()
    if outcome == "FAILED":
        stack.overrides[("kb", "/search")] = lambda _: error(500, "INTERNAL_ERROR")
    if outcome == "BUDGET_STOPPED":
        stack.agent_records = [spent(10_000_000)]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    await asyncio.sleep(0)
    assert m3.status(run_id) == outcome
    assert run_id not in m3.runner.tasks and run_id not in m3.runner._locks
    assert run_id not in m3.runner.deps.event_locks


async def test_requests_for_unknown_runs_leave_no_lock_behind() -> None:
    m3 = Orchestrator(Stack())
    for n in range(5):
        response = await m3.client.post(f"/runs/run_nope{n}/resume")
        assert response.status_code == 404
    assert not m3.runner._locks


# --- partial failures and resume -----------------------------------------------------------------


async def test_lost_approval_checkpoint_creates_no_second_approval_record() -> None:
    stack = thin_stack()
    fail_patch_once(stack, "AWAITING_APPROVAL")
    m3 = Orchestrator(stack, **OVER)
    run_id = await m3.run()
    assert m3.status(run_id) == "RUNNING"
    assert (await m3.post(run_id, "resume")).status_code == 202
    assert m3.status(run_id) == "AWAITING_APPROVAL"
    assert len(stack.approvals) == 1, "an orphan 'pending' approval stays in M2 and on M13"


async def test_answers_changed_after_a_failed_save_score_the_stored_version() -> None:
    """M2 honours Idempotency-Key; a different body must not reuse the same key."""
    stack = Stack()
    stack.questions = [question()]
    by_key: dict[str, httpx.Response] = {}

    async def idempotent(request: httpx.Request) -> httpx.Response:
        key = request.headers["Idempotency-Key"]
        if key not in by_key:
            reply = await stack._profile(request, request.url.path)
            by_key[key] = reply
        return httpx.Response(200, content=by_key[key].content)

    stack.overrides[("profile", "/internal/ideas/idea_1/versions")] = idempotent
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    fail_patch_once(stack, "RUNNING")
    assert (await m3.answer(run_id, "narrow")).status_code == 503
    assert m3.status(run_id) == "AWAITING_INPUT"
    assert (await m3.answer(run_id, "broad")).status_code == 202

    scored_version = m3.state(run_id)["idea_version_id"]
    stored_text = next(v.text for v in stack.ideas["idea_1"].versions if v.id == scored_version)
    assert stored_text == m3.state(run_id)["idea"]["text"]


async def test_long_answers_do_not_erase_the_idea() -> None:
    """Three answers of the allowed 2000 characters each."""
    stack = Stack()
    stack.questions = [question(f"q{n}") for n in (1, 2, 3)]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert (await m3.answer(run_id, "word " * 399)).status_code == 202
    text = m3.state(run_id)["idea"]["text"]
    assert len(text) <= 5000
    assert "flashcards" in text, f"the idea itself is gone, text starts {text[:60]!r}"


async def test_terminal_events_are_not_sent_for_a_state_that_was_not_stored() -> None:
    """A7 'M2 fails mid-run': the run stays resumable, so it must not announce its end."""
    stack = Stack()
    stack.overrides[("kb", "/search")] = lambda _: error(500, "INTERNAL_ERROR")
    fail_patch_once(stack, "FAILED")
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "RUNNING"
    assert not m3.of_type(run_id, RunEventType.RUN_FINISHED)


@pytest.mark.parametrize(
    "step",
    ["EVIDENCE", "FEATURES", "SCORING", "PRELIM_VERDICT", "CHALLENGE", "VERDICT", "CARD"],
)
async def test_resume_after_a_lost_checkpoint_at_any_step_completes(step: str) -> None:
    stack = Stack()
    left = {"n": 3}

    def lose(request: httpx.Request) -> httpx.Response | None:
        if request.method != "PATCH" or not left["n"]:
            return None
        if json.loads(request.content)["state"]["step"] != step:
            return None
        left["n"] -= 1
        return error(500, "INTERNAL_ERROR")

    stack.overrides[("profile", "/internal/runs/")] = lose
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "RUNNING" and left["n"] == 0
    assert (await m3.post(run_id, "resume")).status_code == 202
    assert m3.status(run_id) == "COMPLETED"
    assert len({s.idea_version_id for s in stack.stored}) == 1
    assert len(stack.scored()) == 4  # memoised: no scorer is bought twice


# --- boundaries: paths and logs ------------------------------------------------------------------


@pytest.mark.parametrize("version_id", ["..", "."])
async def test_a_dot_segment_id_cannot_change_the_store_path(version_id: str) -> None:
    stack = Stack()
    m3 = Orchestrator(stack)
    await m3.client.post(
        "/runs",
        json={
            "workflow": "validate",
            "user_id": "u_1",
            "idea_version_id": version_id,
            "profile": profile(),
        },
    )
    assert all(p.startswith("/internal/ideas/versions/") for p in stack.paths("profile")), (
        stack.paths("profile")
    )


async def test_store_error_text_does_not_reach_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    """M2's error message may echo the checkpoint, which holds the idea and the profile."""
    stack = Stack()

    def echo(request: httpx.Request) -> httpx.Response | None:
        if request.method != "PATCH":
            return None
        state = json.loads(request.content)["state"]
        if state["step"] != "SCORING":
            return None
        return httpx.Response(
            422, json={"error": {"code": "VALIDATION_ERROR", "message": state["idea"]["text"]}}
        )

    stack.overrides[("profile", "/internal/runs/")] = echo
    m3 = Orchestrator(stack)
    with caplog.at_level(logging.INFO):
        await m3.run()
    formatter = JsonFormatter("orchestrator")
    lines = "".join(formatter.format(r) for r in caplog.records)
    assert "run paused" in lines
    assert "retype cards" not in lines


# --- A7 rows without a test ----------------------------------------------------------------------


async def test_agentops_outage_does_not_stop_a_run() -> None:
    stack = Stack()
    stack.overrides[("agentops", "/runs")] = lambda _: error(500, "INTERNAL_ERROR")
    m3 = Orchestrator(stack)
    assert m3.status(await m3.run()) == "COMPLETED"


async def test_kb_stats_outage_means_no_cache() -> None:
    stack = Stack()
    stack.overrides[("kb", "/stats")] = lambda _: error(500, "INTERNAL_ERROR")
    m3 = Orchestrator(stack)
    assert m3.status(await m3.run()) == "COMPLETED"
    assert not stack.cache and not stack.redis.values


async def test_unverifiable_check_applies_nothing_and_degrades_the_run() -> None:
    stack = Stack()
    probe = Orchestrator(stack)
    evidence_id = probe.state(await probe.run())["evidence"][0]["evidence_id"]
    stack.cache.clear()
    stack.objections = [
        {
            "objection_id": "o",
            "dimension": "demand_pain",
            "text": "students already do this by hand",
            "evidence_ids": [evidence_id],
            "fatal": True,
        }
    ]
    stack.check_verdicts = {"obj_1": ("unverifiable", "claim check unavailable: model down")}
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.state(run_id)["verdict_request"]["accepted_objections"] == []
    assert "critic could not check every objection" in m3.state(run_id)["degraded"]
    assert not stack.cache


async def test_wording_is_skipped_not_the_card_when_the_budget_runs_out() -> None:
    stack = pivot_stack()

    def burn(_: httpx.Request) -> None:
        stack.agent_records = [spent(10_000_000)]

    stack.overrides[("verdict", "/whatif")] = burn
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "COMPLETED"
    assert "/concretize" not in stack.paths("ideation") and not stack.cache


@pytest.mark.parametrize("action", ["approve", "resume"])
async def test_an_expired_approval_cancels_and_buys_nothing(action: str) -> None:
    stack = thin_stack()
    m3 = Orchestrator(stack, **OVER)
    run_id = await m3.run()
    m3.runner.deps.clock.now += timedelta(days=1, seconds=1)
    if action == "approve":
        response = await m3.approve(run_id)
    else:
        response = await m3.post(run_id, "resume")
    assert response.status_code == 409 and m3.status(run_id) == "CANCELLED"
    assert not stack.paths("scouts")
    assert [a.decision for a in stack.approvals.values()] == ["pending"]


async def test_two_answers_at_once_make_one_new_version() -> None:
    stack = Stack()
    stack.questions = [question()]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    body = {"answers": [{"question_id": "q1", "answer": "broad"}]}
    replies = await asyncio.gather(
        *(m3.client.post(f"/runs/{run_id}/answer", json=body) for _ in range(2))
    )
    await m3.wait(run_id)
    assert sorted(r.status_code for r in replies) == [202, 409]
    assert len(stack.ideas["idea_1"].versions) == 2
