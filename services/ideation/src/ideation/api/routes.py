"""M8 API (architecture §7.2): five agents behind five endpoints."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request

from common import context
from common.contracts.agents import (
    ConcretizeRequest,
    ConcretizeResponse,
    GapAnalyzeRequest,
    GapAnalyzeResponse,
    IdeateRequest,
    IdeateResponse,
    IntakeRequest,
    IntakeResponse,
    SkepticRequest,
    SkepticResponse,
)
from common.http import ServiceError
from common.llm import LLMError, LLMOutputError
from common.service import ApiError
from ideation.services.agents import Agents

router = APIRouter()


async def _run[T](request: Request, run_id: str, call: Callable[[Agents], Awaitable[T]]) -> T:
    if context.safe_id(run_id) is None:
        # the run id names a trace file
        raise ApiError(422, "VALIDATION_ERROR", "run_id has characters that are not allowed")
    # checked here, not in a dependency: a bad body is a 422 even when the service is unready
    agents: Agents | None = request.app.state.agents
    if agents is None:
        raise ApiError(503, "NOT_READY", "the agents are not configured")
    token = context.run_id_var.set(run_id)
    try:
        return await call(agents)
    except LLMOutputError as exc:
        # never free text into the pipeline: the step fails (§12.2)
        raise ApiError(502, "LLM_OUTPUT_INVALID", "the model's reply was unusable") from exc
    except (LLMError, TimeoutError) as exc:
        raise ApiError(502, "LLM_UNAVAILABLE", "the model is unavailable") from exc
    except ServiceError as exc:
        raise ApiError(503, "KB_UNAVAILABLE", "the knowledge base is unavailable") from exc
    finally:
        context.run_id_var.reset(token)


@router.post("/intake")
async def intake(body: IntakeRequest, request: Request) -> IntakeResponse:
    return await _run(request, body.run_id, lambda agents: agents.intake(body))


@router.post("/ideate")
async def ideate(body: IdeateRequest, request: Request) -> IdeateResponse:
    return await _run(request, body.run_id, lambda agents: agents.ideate(body))


@router.post("/gap-analyze")
async def gap_analyze(body: GapAnalyzeRequest, request: Request) -> GapAnalyzeResponse:
    return await _run(request, body.run_id, lambda agents: agents.gap_analyze(body))


@router.post("/skeptic")
async def skeptic(body: SkepticRequest, request: Request) -> SkepticResponse:
    return await _run(request, body.run_id, lambda agents: agents.skeptic(body))


@router.post("/concretize")
async def concretize(body: ConcretizeRequest, request: Request) -> ConcretizeResponse:
    return await _run(request, body.run_id, lambda agents: agents.concretize(body))
