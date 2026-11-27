"""M3 API (architecture §7.2). Challenge and what-if arrive with the Discover phase (Z8)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request, Response

from common import context
from common.contracts.core import RunStatus
from common.contracts.orchestrator import (
    AnswerRequest,
    ApproveRequest,
    ChallengeRequest,
    ChallengeResponse,
    StartRunRequest,
    StartRunResponse,
    WhatIfApplyResponse,
    WhatIfProposalsResponse,
    WhatIfRunRequest,
)
from common.service import ApiError
from orchestrator.services.runner import Runner

router = APIRouter()


def get_runner(request: Request) -> Runner:
    runner: Runner | None = request.app.state.runner
    if runner is None:
        raise ApiError(503, "NOT_READY", "rubrics, prompts or budgets could not be loaded")
    return runner


Service = Annotated[Runner, Depends(get_runner)]
# the id travels on in URLs, headers, Redis keys and trace file names
RunId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
# set by the gateway from the token (§21.2). Required: without it nobody's run can be read
UserId = Annotated[str, Header(alias=context.USER_ID_HEADER, min_length=1, max_length=128)]
IdeaId = Annotated[str, Path(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]


@router.post("/runs", status_code=202)
async def start_run(
    body: StartRunRequest,
    runner: Service,
    user_id: Annotated[str | None, Header(alias=context.USER_ID_HEADER)] = None,
) -> StartRunResponse:
    if user_id is not None and user_id != body.user_id:
        raise ApiError(403, "FORBIDDEN", "a run can only be started for the signed-in user")
    return StartRunResponse(run_id=await runner.create(body))


@router.get("/runs/{run_id}")
async def run_status(run_id: RunId, user_id: UserId, runner: Service) -> RunStatus:
    return await runner.status(run_id, user_id)


@router.post("/runs/{run_id}/answer", status_code=202, response_class=Response)
async def answer(run_id: RunId, user_id: UserId, body: AnswerRequest, runner: Service) -> Response:
    await runner.answer(run_id, user_id, body)
    return Response(status_code=202)


@router.post("/runs/{run_id}/approve", status_code=202, response_class=Response)
async def approve(
    run_id: RunId, user_id: UserId, body: ApproveRequest, runner: Service
) -> Response:
    await runner.approve(run_id, user_id, body)
    return Response(status_code=202)


@router.post("/runs/{run_id}/resume", status_code=202, response_class=Response)
async def resume(run_id: RunId, user_id: UserId, runner: Service) -> Response:
    await runner.resume(run_id, user_id)
    return Response(status_code=202)


def _later() -> ApiError:
    return ApiError(501, "NOT_IMPLEMENTED", "challenge and what-if arrive with phase Z8")


@router.post("/ideas/{idea_id}/challenge", responses={501: {}})
async def challenge(idea_id: IdeaId, body: ChallengeRequest) -> ChallengeResponse:
    raise _later()


@router.post("/ideas/{idea_id}/whatif", responses={501: {}, 202: {"model": WhatIfApplyResponse}})
async def whatif(idea_id: IdeaId, body: WhatIfRunRequest) -> WhatIfProposalsResponse:
    raise _later()
