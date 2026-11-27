"""M12 stub: nothing is checked until Z6, so no claim is reported as supported and every
expensive step needs approval. The input rail lets text through and says that it did not check it.
"""

from fastapi import APIRouter

from common.contracts.agents import (
    ApprovalPolicyRequest,
    ApprovalPolicyResponse,
    CheckRequest,
    CheckResponse,
    ClaimResult,
    InputRailRequest,
    InputRailResponse,
    ScanRequest,
    ScanResponse,
)

STUB = "stub: not implemented yet (Z6)"

router = APIRouter()


@router.post("/check")
async def check(request: CheckRequest) -> CheckResponse:
    return CheckResponse(
        results=[
            ClaimResult(claim_id=c.claim_id, supported=False, verdict="unverifiable", reason=STUB)
            for c in request.claims
        ]
    )


@router.post("/rail/input")
async def rail_input(request: InputRailRequest) -> InputRailResponse:
    return InputRailResponse(allowed=True, reasons=[STUB], redacted_text=request.text)


@router.post("/scan")
async def scan(request: ScanRequest) -> ScanResponse:
    return ScanResponse(flagged=[])


@router.post("/policy/approval")
async def approval(request: ApprovalPolicyRequest) -> ApprovalPolicyResponse:
    return ApprovalPolicyResponse(requires_approval=True, reason=STUB)
