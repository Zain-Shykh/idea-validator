"""M3 API (architecture §7.2). Z1 stubs: runs are replayed from a fixture; answers, approvals
and resume are accepted and ignored.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Request, Response
from fastapi.responses import JSONResponse

from common.contracts.core import Compute, Goal, StudentProfile, Verdict
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
from common.http import ServiceError
from common.service import ApiError
from orchestrator.services.stub_run import StubRunner, new_id

router = APIRouter()


def get_runner(request: Request) -> StubRunner:
    runner: StubRunner = request.app.state.runner
    return runner


Runner = Annotated[StubRunner, Depends(get_runner)]
# a blank id would start a run that cannot build its VerdictRequest
IdeaId = Annotated[str, Path(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]


def _placeholder_profile() -> StudentProfile:
    # challenge and what-if carry no profile; M3 reads it from M2 from Z7 on
    return StudentProfile(
        user_id="stub",
        skills=[],
        hours_per_week=0,
        weeks_available=1,
        team_size=1,
        compute=Compute.LAPTOP,
        goal=Goal.PORTFOLIO,
        interests=[],
    )


@router.post("/runs", status_code=202)
async def start_run(
    request: StartRunRequest, background: BackgroundTasks, runner: Runner
) -> StartRunResponse:
    run_id = new_id("run")
    await runner.start(run_id)
    idea_id = request.idea_version_id or new_id("idea")
    background.add_task(runner.finish, run_id, idea_id, 1, request.profile)
    return StartRunResponse(run_id=run_id)


async def _accept(run_id: str, runner: StubRunner) -> Response:
    if not await runner.events.exists(run_id):
        raise ApiError(404, "NOT_FOUND", "unknown run")
    return Response(status_code=202)


@router.post("/runs/{run_id}/answer", status_code=202, response_class=Response)
async def answer(run_id: str, request: AnswerRequest, runner: Runner) -> Response:
    return await _accept(run_id, runner)


@router.post("/runs/{run_id}/approve", status_code=202, response_class=Response)
async def approve(run_id: str, request: ApproveRequest, runner: Runner) -> Response:
    return await _accept(run_id, runner)


@router.post("/runs/{run_id}/resume", status_code=202, response_class=Response)
async def resume(run_id: str, runner: Runner) -> Response:
    return await _accept(run_id, runner)


async def _verdict_now(runner: StubRunner, idea_id: str, version: int) -> Verdict:
    try:
        return await runner.request_verdict(new_id("run"), idea_id, version, _placeholder_profile())
    except ServiceError as exc:
        raise ApiError(503, "DEPENDENCY_UNAVAILABLE", "the verdict service call failed") from exc


@router.post("/ideas/{idea_id}/challenge")
async def challenge(
    idea_id: IdeaId, request: ChallengeRequest, runner: Runner
) -> ChallengeResponse:
    return ChallengeResponse(
        outcome="removed",
        rationale="stub: no claim can be defended before the critic exists (Z6)",
        verdict=await _verdict_now(runner, idea_id, request.version),
    )


@router.post(
    "/ideas/{idea_id}/whatif",
    response_model=WhatIfProposalsResponse,
    responses={202: {"model": WhatIfApplyResponse}},
)
async def whatif(
    idea_id: IdeaId, request: WhatIfRunRequest, background: BackgroundTasks, runner: Runner
) -> WhatIfProposalsResponse | JSONResponse:
    if request.apply is None:
        return WhatIfProposalsResponse(proposals=[])
    run_id = new_id("run")
    await runner.start(run_id)
    new_version = request.version + 1
    background.add_task(runner.finish, run_id, idea_id, new_version, _placeholder_profile())
    body = WhatIfApplyResponse(run_id=run_id, new_version=new_version)
    return JSONResponse(body.model_dump(mode="json"), status_code=202)
