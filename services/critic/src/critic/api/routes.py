"""M12 API (architecture §7.2). The rail and the scan are pure rules and always answer; the
claim check needs M6 and its prompt, the approval policy needs the budget rules."""

import uuid

from fastapi import APIRouter, Request

from common import context
from common.contracts.agents import (
    ApprovalPolicyRequest,
    ApprovalPolicyResponse,
    CheckRequest,
    CheckResponse,
    FlaggedEvidence,
    InputRailRequest,
    InputRailResponse,
    ScanRequest,
    ScanResponse,
)
from common.injection import scan_text
from common.registry import BudgetConfig
from common.service import ApiError
from common.trace import TraceClient
from critic.services import audit
from critic.services.check import Checker, KbUnavailableError
from critic.services.policy import approval
from critic.services.rail import check_input

router = APIRouter()


def _run_id() -> str:
    """These bodies carry no run id: the caller's header, else one id per request."""
    return context.run_id_var.get() or f"critic-{context.request_id_var.get() or uuid.uuid4().hex}"


@router.post("/check")
async def check(body: CheckRequest, request: Request) -> CheckResponse:
    checker: Checker | None = request.app.state.checker
    if checker is None:
        raise ApiError(503, "NOT_READY", "the claim check is not configured")
    run_id = _run_id()
    token = context.run_id_var.set(run_id)
    try:
        return await checker.check(body, run_id)
    except KbUnavailableError as exc:
        raise ApiError(503, "KB_UNAVAILABLE", "cited evidence could not be looked up") from exc
    finally:
        context.run_id_var.reset(token)


@router.post("/rail/input")
async def rail_input(body: InputRailRequest, request: Request) -> InputRailResponse:
    tracer: TraceClient = request.app.state.tracer
    reply, injected = check_input(body.text, request.app.state.settings.max_input_chars)
    run_id = _run_id()
    audit.injections_blocked(tracer, run_id, int(bool(injected)))
    audit.decision(tracer, run_id, "rail", {"allowed": reply.allowed, "rules": injected})
    return reply


@router.post("/scan")
async def scan(body: ScanRequest, request: Request) -> ScanResponse:
    tracer: TraceClient = request.app.state.tracer
    flagged = [
        FlaggedEvidence(evidence_id=item.evidence_id, card_id=item.card_id, patterns=patterns)
        for item in body.evidence
        if (patterns := scan_text(item.quote, item.source))
    ]
    run_id = _run_id()
    audit.injections_blocked(tracer, run_id, len(flagged))
    facts = {f.evidence_id: f.patterns for f in flagged}
    audit.decision(tracer, run_id, "scan", {"scanned": len(body.evidence), "flagged": facts})
    return ScanResponse(flagged=flagged)


@router.post("/policy/approval")
async def policy_approval(body: ApprovalPolicyRequest, request: Request) -> ApprovalPolicyResponse:
    if context.safe_id(body.run_id) is None:
        # the run id names a trace file
        raise ApiError(422, "VALIDATION_ERROR", "run_id has characters that are not allowed")
    budgets: BudgetConfig | None = request.app.state.budgets
    if budgets is None:
        raise ApiError(503, "NOT_READY", "the budget rules are not loaded")
    reply = approval(body, budgets)
    audit.decision(
        request.app.state.tracer,
        body.run_id,
        "approval_policy",
        {"action": audit.safe(body.action), "requires_approval": reply.requires_approval},
    )
    return reply
