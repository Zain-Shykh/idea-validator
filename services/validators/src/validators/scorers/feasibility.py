"""Feasibility for this student, and learning value. The only scorer that reads the profile."""

import asyncio

from common.contracts.agents import PartialGateInputs, ScorerRequest, ScorerResponse
from common.contracts.base import Amount, ContractModel, Count, Unit
from common.contracts.core import Dimension
from validators.services import features as f
from validators.services.effort import EffortSource
from validators.services.engine import Engine, idea_text


class FeasibilityFeatures(ContractModel):
    available_hours: Count
    est_effort_hours: Amount
    effort_band: tuple[int, int]
    effort_source: EffortSource
    hours_ratio: Amount | None
    required_skills: list[str]
    skill_overlap: Unit | None
    new_skills: list[str]
    compute_fit: bool
    interest_match: Unit | None


async def run(engine: Engine, request: ScorerRequest) -> ScorerResponse:
    deadline = engine.deadline()
    text = idea_text(request.idea)
    profile = request.profile
    hits = await engine.retrieve(request, text)
    builds = [h for h in hits if not f.is_complaint(h.card)]

    # estimated even without evidence: the hours gate needs it (D-006)
    band = f.effort_band(text)
    estimate, source = await engine.effort.estimate(request.idea, band, request.run_id)
    available = f.available_hours(profile)
    required = f.required_skills(text)
    tags = " ".join(tag for h in builds for tag in h.card.niche_tags)
    values = FeasibilityFeatures(
        available_hours=available,
        est_effort_hours=estimate,
        effort_band=band,
        effort_source=source,
        hours_ratio=f.hours_ratio(estimate, available),
        required_skills=sorted(required),
        skill_overlap=f.skill_overlap(profile.skills, required),
        new_skills=f.new_skills(profile.skills, required),
        compute_fit=f.compute_fit(text, profile.compute),
        interest_match=f.interest_match(profile.interests, f"{text} {tags}"),
    ).model_dump(mode="json")

    items = [h.item for h in builds]
    new = ", ".join(values["new_skills"]) or "none detected"
    scores = await asyncio.gather(
        engine.score(
            request,
            Dimension.FEASIBILITY,
            items,
            values,
            f"Estimated {estimate:g} h of {available} h available.",
            deadline,
        ),
        engine.score(request, Dimension.LEARNING, items, values, f"New skills: {new}.", deadline),
    )
    return ScorerResponse(
        scores=list(scores),
        gate_inputs=PartialGateInputs(est_effort_hours=estimate, available_hours=available),
    )
