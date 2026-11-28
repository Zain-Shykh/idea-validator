"""Per-run budget arithmetic (AO-2, M3-4). Pure.

M3 counts steps and pages itself. Tokens and cost are never added by M3: they are the sums of
the run's trace records in M13, where each model call is recorded once by whoever made it.
"""

from common.contracts.agents import CostEstimate
from common.contracts.core import Budget, Spend

_CAPS = (("tokens", "max_tokens"), ("cost_usd", "max_cost_usd"), ("pages", "max_pages"))


def exhausted(spend: Spend, budget: Budget, steps: int = 1) -> list[str]:
    """Why ``steps`` more spending steps may not start; empty when they may. Pages only limit
    page-spending steps, which are estimated and approved on their own."""
    problems = []
    if spend.steps + steps > budget.max_steps:
        problems.append(f"steps: {spend.steps} of {budget.max_steps} used")
    if spend.tokens >= budget.max_tokens:
        problems.append(f"tokens: {spend.tokens} of {budget.max_tokens} used")
    if spend.cost_usd >= budget.max_cost_usd:
        problems.append(f"cost: ${spend.cost_usd:.4f} of ${budget.max_cost_usd:.4f} used")
    return problems


def remaining(spend: Spend, budget: Budget) -> Budget:
    return Budget(
        max_tokens=max(0, budget.max_tokens - spend.tokens),
        max_cost_usd=max(0.0, budget.max_cost_usd - spend.cost_usd),
        max_pages=max(0, budget.max_pages - spend.pages),
        max_steps=max(0, budget.max_steps - spend.steps),
    )


def raised_for(spend: Spend, budget: Budget, estimate: CostEstimate, steps: int = 1) -> Budget:
    """The budget after the student approved ``estimate``: at least enough for that step."""
    values = budget.model_dump()
    for field, cap in _CAPS:
        values[cap] = max(values[cap], getattr(spend, field) + getattr(estimate, field))
    values["max_steps"] = max(values["max_steps"], spend.steps + steps)
    return Budget.model_validate(values)


def deep_search_estimate(pages: int, page_cost_usd: float) -> CostEstimate:
    return CostEstimate(pages=pages, cost_usd=round(pages * page_cost_usd, 6))
