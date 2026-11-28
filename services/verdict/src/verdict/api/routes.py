"""M11 stub: the rules arrive in Z2. Until then no verdict is computed, so every request gets
INSUFFICIENT_EVIDENCE: a canned GO would be a decision nothing made (AVOID-3).
"""

from fastapi import APIRouter

from common.contracts.core import (
    GateResult,
    Gates,
    Sensitivity,
    Stability,
    Thresholds,
    Verdict,
    VerdictLabel,
    VerdictRequest,
    Versions,
    Weights,
)
from common.contracts.verdict import VerdictConfigResponse, WhatIfRequest, WhatIfResponse

ENGINE_VERSION = "m11@stub"
MISSING = "verdict rules are not implemented yet (Z2)"

router = APIRouter()


def stub_verdict(request: VerdictRequest) -> Verdict:
    not_checked = GateResult.NOT_CHECKED
    return Verdict(
        idea_id=request.idea_id,
        version=request.version,
        run_id=request.run_id,
        verdict=VerdictLabel.INSUFFICIENT_EVIDENCE,
        stability=Stability.STABLE,
        score=None,
        confidence=0,
        gates=Gates(
            evidence=not_checked,
            feasibility=not_checked,
            risk=not_checked,
            duplicate=not_checked,
        ),
        # placeholders so the card has a valid shape; Z2 reads them from config/verdict.yaml
        weights=Weights(
            demand_pain=15,
            competition=15,
            differentiation=15,
            feasibility=25,
            risk=10,
            learning=20,
        ),
        thresholds=Thresholds(T_go=70, T_no=50, floor=1.5),
        scores=request.scores,
        weakest_dimension=None,
        what_would_make_go=[],
        sensitivity=Sensitivity(runs=0, flipped=0),
        versions=Versions(
            engine=ENGINE_VERSION,
            rubrics=request.rubric_set,
            profile_weights="stub",
            prompts=request.prompt_versions,
            kb_version=request.kb_version,
        ),
        missing=[MISSING],
    )


@router.post("/verdict")
async def verdict(request: VerdictRequest) -> Verdict:
    return stub_verdict(request)


@router.post("/whatif")
async def whatif(request: WhatIfRequest) -> WhatIfResponse:
    return WhatIfResponse(verdict=stub_verdict(request) if request.mode == "apply" else None)


@router.get("/config")
async def config() -> VerdictConfigResponse:
    return VerdictConfigResponse(
        versions=Versions(engine=ENGINE_VERSION, rubrics="stub", profile_weights="stub"),
        config={"stub": True},
    )
