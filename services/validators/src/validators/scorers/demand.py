"""Demand and pain: complaint cards, their spread and the niche's trend."""

from common.contracts.agents import PartialGateInputs, ScorerRequest, ScorerResponse
from common.contracts.base import ContractModel, Count
from common.contracts.core import Dimension
from validators.services import features as f
from validators.services.engine import Engine, idea_text


class DemandFeatures(ContractModel):
    n_sources: Count
    n_platforms: Count
    complaint_count: Count
    trend_growth: float | None
    recency_days: Count | None


async def run(engine: Engine, request: ScorerRequest) -> ScorerResponse:
    deadline = engine.deadline()
    hits = await engine.retrieve(request, idea_text(request.idea))
    complaints = [h for h in hits if f.is_complaint(h.card)]
    cards = [h.card for h in complaints]
    niche = f.matched_niche(cards, await engine.niches(request.run_id)) if cards else None
    # counted over everything relevant, not only what fits in the prompt
    n_sources, n_platforms = f.count_sources(h.item for h in complaints)
    values = DemandFeatures(
        n_sources=n_sources,
        n_platforms=n_platforms,
        complaint_count=len(complaints),
        trend_growth=niche.trend_slope if niche else None,
        recency_days=f.recency_days(cards, engine.today()),
    )
    score = await engine.score(
        request,
        Dimension.DEMAND_PAIN,
        [h.item for h in f.round_robin(complaints, lambda h: h.item.source)],
        values.model_dump(),
        f"{n_sources} independent sources on {n_platforms} platforms with this complaint.",
        deadline,
    )
    return ScorerResponse(
        scores=[score],
        gate_inputs=PartialGateInputs(n_sources=n_sources, n_platforms=n_platforms),
    )
