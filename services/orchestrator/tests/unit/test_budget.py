"""Budgets and the approval of an expensive deep search (spec Z7, A4 #4, #5)."""

import json

import pytest
from orchestrator_factories import Orchestrator, Stack, error, fixture_cards

from common.contracts.agents import ApprovalPolicyRequest, CostEstimate
from common.contracts.core import Budget, RunEventType, Spend, VerdictLabel
from common.contracts.scouts import ScoutRequest
from orchestrator.services import budget

# over the 20 pages of a Validate run, under the 30-page cap of one deep search
OVER = {"deep_search_pages": 25}


def thin_stack() -> Stack:
    """One card only: below M11's three sources on two platforms."""
    cards = [c for c in fixture_cards() if "flashcard" in c.summary.lower()][:1]
    stack = Stack(cards)
    stack.n_sources, stack.n_platforms = 1, 1
    return stack


def kinds(m3: Orchestrator, run_id: str) -> list[str]:
    return [r["decision"] for r in m3.decisions(run_id, "approval")]


async def test_over_budget_asks_and_waits() -> None:
    stack = thin_stack()
    m3 = Orchestrator(stack, **OVER)
    run_id = await m3.run()

    assert m3.status(run_id) == "AWAITING_APPROVAL"
    (event,) = m3.of_type(run_id, RunEventType.APPROVAL_REQUIRED)
    assert event is m3.events(run_id)[-1] or event == m3.events(run_id)[-1]
    assert event.data is not None
    assert (event.data["estimated_pages"], event.data["estimated_cost_usd"]) == (25, 0.0125)
    assert "$0.0125" in event.message and "25 pages" in event.message
    assert not stack.paths("scouts") and not stack.paths("validators")
    (asked,) = stack.sent("critic/policy/approval", ApprovalPolicyRequest)
    assert asked.action == "deep_search" and asked.remaining.max_pages == 20
    assert [a.decision for a in stack.approvals.values()] == ["pending"]
    assert kinds(m3, run_id) == ["pending"]


async def test_approve_spends_and_searches_again() -> None:
    stack = thin_stack()
    stack.scout_cards = [c for c in fixture_cards() if c.id not in stack.kb.cards]
    m3 = Orchestrator(stack, **OVER)
    run_id = await m3.run()
    stack.n_sources, stack.n_platforms = 4, 3
    assert (await m3.approve(run_id)).status_code == 202

    assert m3.status(run_id) == "COMPLETED"
    (scout,) = stack.sent("scouts/scout", ScoutRequest)
    assert scout.page_budget == 25 and scout.run_id == run_id
    assert len(stack.paths("kb")) and stack.paths("kb").count("/search") == 2
    record = stack.runs[run_id]
    assert record.spend.pages == 5  # what the scout reports it fetched
    assert m3.state(run_id)["budget"]["max_pages"] == 25
    assert m3.path(run_id)[:5] == ["INTAKE", "EVIDENCE", "DEEP_SEARCH", "EVIDENCE", "FEATURES"]
    assert [a.decision for a in stack.approvals.values()] == ["approved"]
    assert kinds(m3, run_id) == ["pending", "approved"]
    assert m3.card(run_id).versions.kb_version == "kb-2"


async def test_decline_continues_without_the_search() -> None:
    stack = thin_stack()
    m3 = Orchestrator(stack, **OVER)
    run_id = await m3.run()
    assert (await m3.approve(run_id, "decline")).status_code == 202

    assert m3.status(run_id) == "COMPLETED" and not stack.paths("scouts")
    card = m3.card(run_id)
    assert card.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE
    assert any("independent sources: found 1" in m for m in card.missing)  # U10
    assert stack.runs[run_id].spend.pages == 0
    assert m3.state(run_id)["budget"]["max_pages"] == 20
    assert [a.decision for a in stack.approvals.values()] == ["declined"]
    assert kinds(m3, run_id) == ["pending", "declined"]
    assert not stack.cache


async def test_within_budget_searches_without_asking_and_only_once() -> None:
    stack = thin_stack()  # the scout finds nothing, so evidence stays thin
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "COMPLETED"
    assert stack.paths("scouts") == ["/scout"] and not stack.approvals
    assert m3.path(run_id).count("DEEP_SEARCH") == 1


async def test_policy_outage_requires_approval() -> None:
    stack = thin_stack()
    stack.overrides[("critic", "/policy")] = lambda _: error(503, "NOT_READY")
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "AWAITING_APPROVAL" and not stack.paths("scouts")


async def test_failed_scout_is_not_repeated() -> None:
    stack = thin_stack()
    stack.overrides[("scouts", "/scout")] = lambda _: error(500, "INTERNAL_ERROR")
    m3 = Orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "COMPLETED" and stack.paths("scouts") == ["/scout"]
    assert "deep search failed" in m3.state(run_id)["degraded"]
    assert stack.runs[run_id].spend.pages == 15  # kept: some may have been fetched


@pytest.mark.parametrize(
    ("cap", "limit"), [("max_steps", 3), ("max_tokens", 100), ("max_cost_usd", 0.001)]
)
async def test_hard_cap_stops_run(cap: str, limit: float) -> None:
    from orchestrator_factories import NOW

    from common.contracts.core import TraceKind, TraceRecord

    stack = Stack()
    if cap != "max_steps":
        stack.agent_records = [
            TraceRecord(
                run_id="r",
                step_id=1,
                agent="ideation",
                kind=TraceKind.LLM_CALL,
                tokens_in=500,
                cost_usd=0.01,
                ts=NOW,
            )
        ]
    m3 = Orchestrator(stack)
    rules = m3.runner.deps.rules
    workflow = next(w for w in rules.budgets if w.value == "validate")
    rules.budgets[workflow] = rules.budgets[workflow].model_copy(update={cap: limit})
    run_id = await m3.run()

    assert m3.status(run_id) == "BUDGET_STOPPED"
    assert not stack.paths("validators") and not stack.stored
    problem, finished = m3.events(run_id)[-2:]
    assert problem.data is not None and problem.data["code"] == "BUDGET_EXCEEDED"
    assert finished.type is RunEventType.RUN_FINISHED
    assert not stack.redis.sets.get("orch:active-runs")


def test_budget_arithmetic() -> None:
    cap = Budget(max_tokens=100, max_cost_usd=1, max_pages=10, max_steps=5)
    assert budget.exhausted(Spend(steps=4), cap, 1) == []
    assert budget.exhausted(Spend(steps=4), cap, 2)
    assert budget.exhausted(Spend(tokens=100), cap)
    assert budget.exhausted(Spend(cost_usd=1), cap)
    assert budget.exhausted(Spend(pages=99), cap) == []  # pages are estimated per search
    left = budget.remaining(Spend(tokens=200, pages=4), cap)
    assert (left.max_tokens, left.max_pages) == (0, 6)
    raised = budget.raised_for(Spend(pages=8, steps=5), cap, CostEstimate(pages=25))
    assert (raised.max_pages, raised.max_steps, raised.max_tokens) == (33, 6, 100)
    assert json.loads(budget.deep_search_estimate(25, 0.0005).model_dump_json()) == {
        "cost_usd": 0.0125,
        "pages": 25,
        "tokens": 0,
    }
