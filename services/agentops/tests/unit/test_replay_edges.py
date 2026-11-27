"""Replay of runs with an uneven history (spec Z10, A4 #9 and A7). Every run is recorded with M3
on the fake stack (made-up cards and scores, real M11)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from orchestrator_factories import IDEA_TEXT, Orchestrator, Stack, error, idea, question
from test_replay import again, assert_same, recorded_run, records_of

from agentops.replay import main, replay
from common.contracts.core import TraceRecord


async def clarifying() -> tuple[Orchestrator, str]:
    stack = Stack()
    stack.questions = [question("q1", "differentiation")]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "AWAITING_INPUT"
    stack.scores = {"differentiation": 1.0}
    return m3, run_id


async def test_a_refused_answer_before_the_accepted_one_does_not_break_replay() -> None:
    m3, run_id = await clarifying()
    assert (await m3.answer(run_id, "REJECT this")).status_code == 422
    await m3.answer(run_id, "broad")
    assert m3.status(run_id) == "COMPLETED"

    assert_same(await again(records_of(m3, run_id), run_id))


async def test_an_answer_repeated_after_the_idea_store_failed_replays() -> None:
    m3, run_id = await clarifying()
    m3.stack.overrides[("profile", "/internal/ideas/")] = lambda r: (
        error(503, "DOWN") if r.method == "POST" else None
    )
    assert (await m3.answer(run_id, "broad")).status_code == 503
    m3.stack.overrides.clear()
    await m3.answer(run_id, "broad")
    assert m3.status(run_id) == "COMPLETED"

    assert_same(await again(records_of(m3, run_id), run_id))


async def test_a_scorer_outage_while_others_are_in_flight_replays() -> None:
    """The recorded run cancels the three slower scorers; they have no reply in the trace."""
    stack = Stack()
    stack.scorer_delay_s = 0.2
    stack.overrides[("validators", "/score/demand")] = lambda _: error(422, "KB_UNAVAILABLE")
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "FAILED"

    assert_same(await again(records_of(m3, run_id), run_id), "FAILED")


async def test_scorers_that_finish_out_of_order_replay() -> None:
    stack = Stack()

    def delayed(seconds: float):  # type: ignore[no-untyped-def]
        async def wait(_: httpx.Request) -> None:
            await asyncio.sleep(seconds)

        return wait

    for seconds, name in enumerate(("demand", "competition", "feasibility", "risk")):
        stack.overrides[("validators", f"/score/{name}")] = delayed(0.2 - 0.05 * seconds)
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    records = records_of(m3, run_id)
    finished = [r.tool for r in records if (r.tool or "").startswith("score_")]
    assert len(finished) == 4

    assert_same(await again(records, run_id))


async def test_the_children_of_a_discover_run_replay_but_the_parent_does_not() -> None:
    stack = Stack()
    stack.idea_rounds = [[idea("Diagram cards"), idea("Formula cards")]]
    m3 = Orchestrator(stack)
    parent = await m3.discover(max_ideas=2)
    children = m3.children(parent)
    assert len(children) == 2

    for child in children:
        assert_same(await again(records_of(m3, child), child))
    assert main_exit(records_of(m3, parent), parent) == 2


def main_exit(records: list[TraceRecord], run_id: str) -> int:
    """``main`` from inside a running loop: in a thread of its own."""
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "trace.jsonl"
        path.write_text("".join(r.model_dump_json() + "\n" for r in records))
        with ThreadPoolExecutor(1) as pool:
            return pool.submit(main, [run_id, "--trace-file", str(path)]).result()


async def test_a_resumed_trace_with_repeated_records_is_reported_not_accepted() -> None:
    """After a crash M3 repeats a step: a second ``run_input`` and the same calls again."""
    records, run_id = await recorded_run()
    next_id = max(r.step_id for r in records)
    repeated = [
        r.model_copy(update={"step_id": next_id + n})
        for n, r in enumerate((r for r in records if r.tool in ("run_input", "search_kb")), 1)
    ]

    result = await again(records + repeated, run_id)

    assert "search_kb: 1 recorded call(s) not made" in result.differences
    assert result.verdict == result.recorded_verdict


def lines(records: list[TraceRecord]) -> bytes:
    return "".join(r.model_dump_json() + "\n" for r in records).encode()


def tampered(records: list[TraceRecord], tool: str, **changes: str) -> list[TraceRecord]:
    return [r.model_copy(update=changes) if r.tool == tool else r for r in records]


@pytest.mark.parametrize(
    "case",
    ["verdict request not JSON", "verdict request not an object", "not UTF-8", "deep JSON"],
)
def test_a_damaged_trace_means_cannot_replay_or_differs_never_a_traceback(
    tmp_path: Path, case: str
) -> None:
    """Exit 1 means "the run differs": an unreadable record must give 2, not crash."""
    records, run_id = asyncio.run(recorded_run())
    path = tmp_path / "FAKE_trace.jsonl"
    if case == "verdict request not JSON":
        path.write_bytes(lines(tampered(records, "prelim_verdict", input="{secret words")))
    elif case == "verdict request not an object":
        path.write_bytes(lines(tampered(records, "prelim_verdict", input='["secret words"]')))
    elif case == "not UTF-8":
        path.write_bytes(lines(records) + b"\xff\xfe\n")
    else:
        deep = "[" * 200_000 + "]" * 200_000
        path.write_bytes(lines(tampered(records, "search_kb", input=deep)))

    # deeply nested JSON ends the replayed run, which is a difference and not a crash
    allowed = (1, 2) if case == "deep JSON" else (2,)
    assert main([run_id, "--trace-file", str(path)]) in allowed


async def test_student_text_stays_off_stdout_and_logs_through_a_clarified_run(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    m3, run_id = await clarifying()
    await m3.answer(run_id, "XYZZY-PRIVATE-ANSWER")
    assert m3.status(run_id) == "COMPLETED"
    records = records_of(m3, run_id)
    # the full text is in the M13 record
    (answer,) = [r for r in records if r.tool == "student_answer"]
    assert "XYZZY-PRIVATE-ANSWER" in (answer.input or "")
    (start,) = [r for r in records if r.tool == "run_input"]
    assert json.loads(start.input or "")["idea"]["text"] == IDEA_TEXT

    result = await replay(records, run_id, settings=m3.runner.deps.settings)

    assert_same(result)
    printed = capsys.readouterr()
    for out in (m3.lines.getvalue(), caplog.text, printed.out, printed.err):
        assert "XYZZY-PRIVATE-ANSWER" not in out
        assert "retype cards" not in out
