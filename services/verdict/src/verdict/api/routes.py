"""HTTP only: parse, hand over to the engine, map errors. All rules are in ``verdict.engine``."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from common.contracts.core import Verdict, VerdictRequest, Versions
from common.contracts.verdict import VerdictConfigResponse, WhatIfRequest, WhatIfResponse
from common.service import ApiError
from verdict.config import VerdictConfig
from verdict.engine import apply_changes, decide

router = APIRouter()


def active_config(request: Request) -> VerdictConfig:
    config: VerdictConfig | None = request.app.state.config
    if config is None:
        raise ApiError(503, "NOT_READY", "No valid verdict config is loaded")
    return config


Config = Annotated[VerdictConfig, Depends(active_config)]


@router.post("/verdict")
async def verdict(request: VerdictRequest, config: Config) -> Verdict:
    return decide(request, config)


@router.post("/whatif")
async def whatif(request: WhatIfRequest, config: Config) -> WhatIfResponse:
    if request.mode == "search":
        return WhatIfResponse(candidates=decide(request, config).what_would_make_go)
    try:
        return WhatIfResponse(verdict=apply_changes(request, request.changes, config))
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_ERROR", str(exc)) from exc


@router.get("/config")
async def active_rules(config: Config) -> VerdictConfigResponse:
    return VerdictConfigResponse(
        versions=Versions(
            engine=f"m11@{config.version}",
            rubrics="per-request",  # M11 gets the rubric set with every request
            profile_weights=config.weights_version,
        ),
        config=config.model_dump(mode="json"),
    )
