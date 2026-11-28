"""Approval above budget (M12-4, AO-2): a pure rule over ``config/budgets.yaml``."""

from common.contracts.agents import ApprovalPolicyRequest, ApprovalPolicyResponse
from common.registry import BudgetConfig

# estimate field -> budget field
_LIMITS = {"cost_usd": "max_cost_usd", "pages": "max_pages", "tokens": "max_tokens"}


def _number(value: float) -> str:
    # an int may be too large for a float, which ":g" would convert it to
    return str(value) if isinstance(value, int) else f"{value:g}"


def approval(request: ApprovalPolicyRequest, budgets: BudgetConfig) -> ApprovalPolicyResponse:
    """Equal to a limit is within it. An action without a cap is never approved silently."""
    reasons = []
    cap = budgets.actions.get(request.action)
    if cap is None:
        reasons.append("this action has no cap in the budget rules")
    for field, limit in _LIMITS.items():
        wanted = getattr(request.estimate, field)
        left = getattr(request.remaining, limit)
        if wanted > left:
            reasons.append(
                f"estimated {field} {_number(wanted)} is above the {_number(left)} left in this run"
            )
        allowed = None if cap is None else getattr(cap, limit)
        if allowed is not None and wanted > allowed:
            reasons.append(
                f"estimated {field} {_number(wanted)} is above the cap of {_number(allowed)}"
            )
    return ApprovalPolicyResponse(
        requires_approval=bool(reasons),
        reason="; ".join(reasons) or f"within the remaining budget and the cap ({budgets.version})",
    )
