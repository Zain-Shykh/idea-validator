"""M10 API (API-I3, architecture §7.2)."""

from fastapi import APIRouter, Request

from common.contracts.agents import ScoreRequest
from common.contracts.core import DimensionScore
from common.service import ApiError
from scorer.services.scoring import Scorer, UnknownRubricError

router = APIRouter()


@router.post("/score")
async def score(body: ScoreRequest, request: Request) -> DimensionScore:
    scorer: Scorer | None = request.app.state.scorer
    if scorer is None:
        raise ApiError(503, "NOT_READY", "rubrics or prompt could not be loaded")
    try:
        return await scorer.score(body)
    except UnknownRubricError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "unknown rubric id or version") from exc
