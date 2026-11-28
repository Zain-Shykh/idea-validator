"""M9 API (architecture §7.2): ``POST /score/{scorer}``."""

from fastapi import APIRouter, Request

from common import context
from common.contracts.agents import ScorerRequest, ScorerResponse
from common.service import ApiError
from validators.scorers import SCORERS
from validators.services.engine import Engine, KbUnavailableError

router = APIRouter()


@router.post("/score/{scorer}")
async def score(scorer: str, body: ScorerRequest, request: Request) -> ScorerResponse:
    if scorer not in SCORERS:
        raise ApiError(404, "NOT_FOUND", "unknown scorer", {"scorers": sorted(SCORERS)})
    if context.safe_id(body.run_id) is None:
        # the run id travels on as a header and names a trace file
        raise ApiError(422, "VALIDATION_ERROR", "run_id has characters that are not allowed")
    engine: Engine | None = request.app.state.engine
    if engine is None:
        raise ApiError(503, "NOT_READY", "validators are not configured")
    token = context.run_id_var.set(body.run_id)
    try:
        return await SCORERS[scorer](engine, body)
    except KbUnavailableError as exc:
        raise ApiError(503, "KB_UNAVAILABLE", "the knowledge base could not be searched") from exc
    finally:
        context.run_id_var.reset(token)
