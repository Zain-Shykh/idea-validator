"""M10 stub: no model is served until Z4, so every dimension abstains."""

from fastapi import APIRouter

from common.contracts.agents import ScoreRequest
from common.contracts.core import Dimension, DimensionScore
from common.service import ApiError

router = APIRouter()


@router.post("/score")
async def score(request: ScoreRequest) -> DimensionScore:
    name = request.rubric_id.split("@")[0]
    if name not in Dimension:
        raise ApiError(422, "VALIDATION_ERROR", "unknown rubric dimension")
    return DimensionScore(
        dimension=Dimension(name),
        score=None,
        confidence=0,
        evidence_ids=[],
        reason="stub: no scoring model yet (Z4)",
        rubric_version=request.rubric_id,
        model="stub",
    )
