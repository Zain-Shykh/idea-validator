"""Verdict cache and step memo (M2-4, M2-5, M3-5, AVOID-2; spec Z7, A4 #7-9)."""

from dataclasses import replace

import pytest
from orchestrator_factories import Orchestrator, Stack, error

from common.contracts.core import RunEventType, TraceKind


def verdict_lookups(m3: Orchestrator, run_id: str) -> list[bool]:
    return [
        r.cache_hit
        for r in m3.stack.records
        if r.run_id == run_id and r.tool == "verdict_cache" and r.kind is TraceKind.TOOL_CALL
    ]


async def test_identical_rerun_hits_the_cache() -> None:
    stack = Stack()
    m3 = Orchestrator(stack)
    first = await m3.run()
    stack.calls.clear()
    second = await m3.run()

    assert m3.path(second) == ["INTAKE", "CARD"]
    assert not stack.paths("validators") and not stack.paths("ideation")
    assert verdict_lookups(m3, first) == [False] and verdict_lookups(m3, second) == [True]
    (event,) = m3.of_type(second, RunEventType.CACHE_HIT)
    assert event.data == {"tool": "verdict_cache"}
    old, new = m3.card(first), m3.card(second)
    assert new.from_cache and not old.from_cache and new.run_id == second
    assert new.model_copy(update={"from_cache": False, "run_id": first}) == old
    assert len(stack.stored) == 2 and len(stack.cache) == 1
    assert stack.runs[second].spend.steps == 0
    assert [r.tool for r in stack.records if r.run_id == second and r.tool == "verdict"]


@pytest.mark.parametrize("change", ["profile", "rubric_set", "kb_version"])
async def test_a_new_version_misses_the_cache(change: str) -> None:
    stack = Stack()
    m3 = Orchestrator(stack)
    await m3.run()
    changes = {}
    if change == "profile":
        changes = {"version": 2}
    elif change == "rubric_set":
        m3.runner.deps.rules = replace(m3.runner.deps.rules, rubric_set="r2")
    else:
        stack.kb_version = "kb-2"
    second = await m3.run(**changes)
    assert verdict_lookups(m3, second) == [False] and not m3.card(second).from_cache
    assert len(stack.cache) == 2


async def test_profile_change_rescores_feasibility_only() -> None:
    stack = Stack()
    m3 = Orchestrator(stack)
    await m3.run()
    stack.calls.clear()
    second = await m3.run(hours_per_week=20, version=2)
    assert stack.scored() == ["feasibility"]
    assert "/search" not in stack.paths("kb")  # the evidence search is reused too
    hits = {r.tool for r in stack.records if r.run_id == second and r.cache_hit}
    assert hits == {"search_kb", "score_demand", "score_competition", "score_risk"}
    assert m3.status(second) == "COMPLETED"


async def test_no_kb_version_means_no_cache_and_no_memo() -> None:
    stack = Stack()
    stack.kb_version = None
    m3 = Orchestrator(stack)
    await m3.run()
    await m3.run()
    assert len(stack.scored()) == 8 and not stack.cache and not stack.redis.values


@pytest.mark.parametrize(
    "setup",
    ["fallback_model", "failed_scorer", "midpoint_effort", "skeptic_down", "model_none"],
)
async def test_degraded_runs_are_not_cached(setup: str) -> None:
    stack = Stack()
    if setup == "fallback_model":
        stack.score_model = "scorer-hosted"
    elif setup == "failed_scorer":
        stack.overrides[("validators", "/score/risk")] = lambda _: error(500, "INTERNAL_ERROR")
    elif setup == "skeptic_down":
        stack.overrides[("ideation", "/skeptic")] = lambda _: error(500, "INTERNAL_ERROR")
    else:
        original = stack.scorer_response

        def changed(name, body):  # type: ignore[no-untyped-def]
            reply = original(name, body)
            if setup == "midpoint_effort" and name == "feasibility":
                scores = [
                    s.model_copy(update={"features": {"effort_source": "band_midpoint"}})
                    for s in reply.scores
                ]
            elif setup == "model_none" and name == "risk":
                scores = [
                    s.model_copy(
                        update={
                            "score": None,
                            "confidence": 0,
                            "evidence_ids": [],
                            "model": "none",
                            "reason": "no evidence",
                        }
                    )
                    for s in reply.scores
                ]
            else:
                return reply
            return reply.model_copy(update={"scores": scores})

        stack.scorer_response = changed  # type: ignore[method-assign]
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "COMPLETED"
    assert not stack.cache
    assert m3.decisions(run_id, "cache")[-1]["stored"] is False


async def test_failed_scorer_results_are_not_memoised() -> None:
    stack = Stack()
    stack.overrides[("validators", "/score/risk")] = lambda _: error(500, "INTERNAL_ERROR")
    m3 = Orchestrator(stack)
    await m3.run()
    stack.overrides.clear()
    stack.calls.clear()
    run_id = await m3.run()
    assert stack.scored() == ["risk"] and m3.status(run_id) == "COMPLETED"


async def test_cache_outage_does_not_stop_a_run() -> None:
    stack = Stack()
    stack.overrides[("profile", "/internal/cache")] = lambda _: error(500, "INTERNAL_ERROR")
    m3 = Orchestrator(stack)
    assert m3.status(await m3.run()) == "COMPLETED"
