from typing import Any

import pytest
from critic_factories import ROOT

from common.contracts.agents import ApprovalPolicyRequest
from common.registry import load_budgets
from critic.services.policy import approval

BUDGETS = load_budgets(ROOT / "config/budgets.yaml")
LEFT = {"max_tokens": 5000, "max_cost_usd": 0.04, "max_pages": 25, "max_steps": 10}


def ask(action: str = "deep_search", left: dict[str, Any] | None = None, **estimate: Any) -> Any:
    body = ApprovalPolicyRequest(
        run_id="run_1", action=action, estimate=estimate, remaining=left or LEFT
    )
    return approval(body, BUDGETS)


@pytest.mark.parametrize(
    "estimate",
    [{}, {"pages": 25}, {"cost_usd": 0.04}, {"tokens": 5000}, {"pages": 10, "cost_usd": 0.01}],
)
def test_within_budget_needs_no_approval(estimate: dict[str, Any]) -> None:
    reply = ask(**estimate)
    assert not reply.requires_approval and "v1" in reply.reason


@pytest.mark.parametrize("estimate", [{"pages": 26}, {"cost_usd": 0.041}, {"tokens": 5001}])
def test_above_the_remaining_budget_needs_approval(estimate: dict[str, Any]) -> None:
    reply = ask(**estimate)
    assert reply.requires_approval and "left in this run" in reply.reason


def test_above_the_action_cap_needs_approval() -> None:
    plenty = {"max_tokens": 10**6, "max_cost_usd": 5, "max_pages": 500, "max_steps": 10}
    assert not ask(left=plenty, pages=30, cost_usd=0.05).requires_approval
    reply = ask(left=plenty, pages=31)
    assert reply.requires_approval and "above the cap of 30" in reply.reason
    assert ask("llm_call", left=plenty, tokens=8001).requires_approval


def test_unknown_action_needs_approval() -> None:
    reply = ask("buy_a_server")
    assert reply.requires_approval and "no cap" in reply.reason
