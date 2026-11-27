"""``agentops.replay`` re-runs a recorded run offline (AO-1; spec Z10, A4 #9). Runs are recorded
with M3 on the fake stack (real M11); replay gets the records and nothing else."""

import socket
from pathlib import Path
from typing import Any

import pytest
from orchestrator_factories import (
    Orchestrator,
    Stack,
    error,
    fixture_cards,
    question,
    settings,
)

from agentops.replay import ReplayError, ReplayResult, main, replay
from common.contracts.core import TraceRecord

ROOT = Path(__file__).resolve().parents[4]
OBJECTION = {
    "objection_id": "o",
    "dimension": "competition",
    "text": "several tools already do this",
    "fatal": False,
}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("replay tried to connect")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def records_of(m3: Orchestrator, run_id: str) -> list[TraceRecord]:
    return [r for r in m3.stack.records if r.run_id == run_id]


async def again(records: list[TraceRecord], run_id: str, **changes: Any) -> ReplayResult:
    return await replay(records, run_id, settings=settings(**changes))


def assert_same(result: ReplayResult, status: str = "COMPLETED") -> None:
    assert result.differences == []
    assert result.status == result.recorded_status == status
    assert result.verdict == result.recorded_verdict
    assert (result.verdict is not None) == (status == "COMPLETED")


async def test_a_completed_run_replays_to_the_same_verdict(offline: None) -> None:
    stack = Stack()
    stack.objections = [OBJECTION | {"evidence_ids": [f"{next(iter(stack.kb.cards))}#c0"]}]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "COMPLETED"
    calls = len(stack.calls)

    result = await again(records_of(m3, run_id), run_id)

    assert_same(result)
    assert result.verdict == m3.card(run_id).verdict.value
    assert len(stack.calls) == calls


async def test_a_clarified_run_replays_with_the_recorded_answers(offline: None) -> None:
    stack = Stack()
    stack.questions = [question("q1", "differentiation")]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    stack.scores = {"differentiation": 1.0}
    await m3.answer(run_id, "broad")
    assert m3.status(run_id) == "COMPLETED"

    result = await again(records_of(m3, run_id), run_id)

    assert_same(result)
    assert result.verdict == m3.card(run_id).verdict.value


@pytest.mark.parametrize("decision", ["approve", "decline"])
async def test_a_run_with_an_approval_replays(offline: None, decision: str) -> None:
    cards = [c for c in fixture_cards() if "flashcard" in c.summary.lower()][:1]
    stack = Stack(cards)
    stack.n_sources, stack.n_platforms = 1, 1
    m3 = Orchestrator(stack, deep_search_pages=25)
    run_id = await m3.run()
    assert m3.status(run_id) == "AWAITING_APPROVAL"
    await m3.approve(run_id, decision)
    assert m3.status(run_id) == "COMPLETED"

    assert_same(await again(records_of(m3, run_id), run_id, deep_search_pages=25))


async def test_memo_hits_and_a_failed_scorer_replay(offline: None) -> None:
    m3 = Orchestrator(Stack())
    await m3.run()
    m3.stack.overrides[("validators", "/score/feasibility")] = lambda _: error(503, "BUSY")
    run_id = await m3.run(version=2)  # everything but feasibility comes from the memo
    reused = {r.tool for r in records_of(m3, run_id) if r.cache_hit}
    assert {"search_kb", "score_demand", "score_risk"} <= reused
    assert m3.status(run_id) == "COMPLETED"

    result = await again(records_of(m3, run_id), run_id)

    assert_same(result)
    assert result.verdict == "INSUFFICIENT_EVIDENCE"


async def test_a_failed_run_replays_to_the_same_failure(offline: None) -> None:
    stack = Stack()
    stack.overrides[("kb", "/search")] = lambda _: error(503, "KB_DOWN")
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "FAILED"

    assert_same(await again(records_of(m3, run_id), run_id), "FAILED")


async def recorded_run() -> tuple[list[TraceRecord], str]:
    m3 = Orchestrator(Stack())
    run_id = await m3.run()
    return records_of(m3, run_id), run_id


async def test_changed_rules_show_as_a_difference(offline: None, tmp_path: Path) -> None:
    records, run_id = await recorded_run()
    rules = tmp_path / "verdict.yaml"
    text = (ROOT / "config/verdict.yaml").read_text()
    rules.write_text(text.replace("T_go: 70", "T_go: 95"))

    result = await replay(records, run_id, settings=settings(), verdict_config=rules)

    assert result.recorded_verdict == "GO" and result.verdict == "PIVOT"
    assert any("M11 decides differently" in d for d in result.differences)


async def test_a_tampered_reply_is_reported(offline: None) -> None:
    records, run_id = await recorded_run()
    changed = [
        r.model_copy(update={"reply": r.reply.replace('"score":4.0', '"score":1.0')})
        if r.tool == "score_risk" and r.reply
        else r
        for r in records
    ]

    result = await again(changed, run_id)

    assert "prelim_verdict: the request differs from the recorded one" in result.differences


async def test_a_missing_record_is_reported(offline: None) -> None:
    records, run_id = await recorded_run()

    result = await again([r for r in records if r.tool != "search_kb"], run_id)

    assert "search_kb: a call the recorded run did not make" in result.differences
    assert result.status == "FAILED"


async def test_runs_that_cannot_be_replayed_say_why(offline: None) -> None:
    m3 = Orchestrator(Stack())
    await m3.run()
    cached = await m3.run()
    with pytest.raises(ReplayError, match="cache"):
        await again(records_of(m3, cached), cached)
    records, run_id = await recorded_run()
    with pytest.raises(ReplayError, match="run_input"):
        await again([r for r in records if r.tool != "run_input"], run_id)
    with pytest.raises(ReplayError, match="no trace records"):
        await again(records, "run_other")


def test_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    records, run_id = asyncio.run(recorded_run())
    path = tmp_path / "trace.jsonl"
    path.write_text("".join(r.model_dump_json() + "\n" for r in records))
    args = [run_id, "--trace-file", str(path)]

    assert main(args) == 0
    assert "same requests, decisions and verdict" in capsys.readouterr().out
    rules = tmp_path / "verdict.yaml"
    rules.write_text((ROOT / "config/verdict.yaml").read_text().replace("T_go: 70", "T_go: 95"))
    assert main([*args, "--verdict-config", str(rules)]) == 1
    assert main(["run_missing", "--trace-file", str(path)]) == 2
    assert main(["../etc", "--trace-file", str(path)]) == 2
    path.write_text('{"idea": "secret text"}\n')
    assert main(args) == 2
    assert "secret text" not in capsys.readouterr().err
