"""M8 stub: no model is called. Ideas only restate supplied gaps and keep their evidence ids."""

from fastapi import APIRouter

from common.contracts.agents import (
    ConcreteChange,
    ConcretizeRequest,
    ConcretizeResponse,
    EnrichedIdea,
    GapAnalyzeRequest,
    GapAnalyzeResponse,
    IdeaSlots,
    IdeateRequest,
    IdeateResponse,
    IntakeRequest,
    IntakeResponse,
    SkepticRequest,
    SkepticResponse,
)
from common.contracts.core import GeneratedIdea

MAX_IDEAS = 6

router = APIRouter()


@router.post("/intake")
async def intake(request: IntakeRequest) -> IntakeResponse:
    slots = IdeaSlots(target_user="", platform="", core_features=[], scope="")
    return IntakeResponse(slots=slots, questions=[])


@router.post("/ideate")
async def ideate(request: IdeateRequest) -> IdeateResponse:
    return IdeateResponse(
        ideas=[
            GeneratedIdea(
                title=f"Stub idea for niche {gap.niche_id}",
                text=gap.statement,
                niche_id=gap.niche_id,
                target_user="unknown (stub)",
                gap_evidence_ids=[item.evidence_id for item in gap.evidence],
                rationale="stub: restates the gap, no model was called",
            )
            for gap in request.gaps[:MAX_IDEAS]
        ]
    )


@router.post("/gap-analyze")
async def gap_analyze(request: GapAnalyzeRequest) -> GapAnalyzeResponse:
    return GapAnalyzeResponse(ideas=[EnrichedIdea(idea=idea) for idea in request.ideas])


@router.post("/skeptic")
async def skeptic(request: SkepticRequest) -> SkepticResponse:
    return SkepticResponse(objections=[])


@router.post("/concretize")
async def concretize(request: ConcretizeRequest) -> ConcretizeResponse:
    return ConcretizeResponse(
        changes=[
            ConcreteChange(dimension=c.dimension, change="stub: no concrete change yet (Z8)")
            for c in request.changes
        ]
    )
