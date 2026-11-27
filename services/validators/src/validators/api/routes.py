"""M9 stub: every scorer abstains until Z5 (M9-4: no evidence checked, no score)."""

from fastapi import APIRouter

from common.contracts.agents import ScorerRequest, ScorerResponse
from common.contracts.core import Dimension, DimensionScore
from common.service import ApiError

SCORERS = {
    "demand": (Dimension.DEMAND_PAIN,),
    "competition": (Dimension.COMPETITION, Dimension.DIFFERENTIATION),
    "feasibility": (Dimension.FEASIBILITY, Dimension.LEARNING),
    "risk": (Dimension.RISK,),
}

router = APIRouter()


@router.post("/score/{scorer}")
async def score(scorer: str, request: ScorerRequest) -> ScorerResponse:
    if scorer not in SCORERS:
        raise ApiError(404, "NOT_FOUND", "unknown scorer", {"scorers": sorted(SCORERS)})
    return ScorerResponse(
        scores=[
            DimensionScore(
                dimension=dimension,
                score=None,
                confidence=0,
                evidence_ids=[],
                reason="stub: scorer not implemented yet (Z5)",
                rubric_version=f"{dimension}@v1",
                model="stub",
            )
            for dimension in SCORERS[scorer]
        ]
    )
