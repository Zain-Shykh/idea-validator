"""Competition and differentiation: product cards, the niche's gaps and the duplicate check."""

import asyncio

from pydantic import Field

from common.contracts.agents import PartialGateInputs, ScorerRequest, ScorerResponse
from common.contracts.base import CardId, ContractModel, Count, Unit
from common.contracts.core import Dimension, DuplicateCandidate, EvidenceItem
from validators.services import features as f
from validators.services.engine import Engine, Hit, card_label, idea_text

_NAMED = 3


class Competitor(ContractModel):
    """Always a knowledge-base card (AVOID-4)."""

    card_id: CardId
    name: str
    similarity: Unit
    funded: bool


class CompetitionFeatures(ContractModel):
    n_competitors: Count
    funded_competitors: Count
    max_similarity: Unit | None
    gap_coverage: Unit | None
    competitors: list[Competitor] = Field(default_factory=list)


def _duplicate(engine: Engine, top: Hit | None, text: str) -> DuplicateCandidate | None:
    if (
        top is None
        or top.similarity is None
        or top.similarity < engine.settings.duplicate_similarity
        or not f.same_customer(top.card.customer, text)
    ):
        return None
    return DuplicateCandidate(
        card_id=top.card.id,
        product_name=top.card.name,
        same_core_features=True,  # what the duplicate threshold stands for (spec A9)
        same_user=True,
        similarity=top.similarity,
    )


async def run(engine: Engine, request: ScorerRequest) -> ScorerResponse:
    deadline = engine.deadline()
    text = idea_text(request.idea)
    hits = await engine.retrieve(request, text)
    products = sorted(
        (h for h in hits if not f.is_complaint(h.card)), key=lambda h: -(h.similarity or 0)
    )
    scored = [(h, h.similarity) for h in products if h.similarity is not None]
    rivals = [(h, s) for h, s in scored if s >= engine.settings.competitor_similarity]

    niche = None
    if products:
        niche = f.matched_niche((h.card for h in products), await engine.niches(request.run_id))
    gaps = await engine.gaps(request.run_id, niche.niche_id) if niche else []
    gap_evidence: list[EvidenceItem] = []
    if products and gaps:
        gap_evidence = await engine.resolve(request.run_id, [g.evidence_ids[0] for g in gaps])

    values = CompetitionFeatures(
        n_competitors=len(rivals),
        funded_competitors=sum((h.card.funding_usd or 0) > 0 for h, _ in rivals),
        max_similarity=max((s for _, s in scored), default=None),
        gap_coverage=f.gap_coverage(
            gaps, [f"{h.card.summary} {h.card.problem} {h.card.text}" for h in products]
        ),
        competitors=[
            Competitor(
                card_id=h.card.id,
                name=h.card.name,
                similarity=s,
                funded=(h.card.funding_usd or 0) > 0,
            )
            for h, s in rivals
        ],
    )
    named = ", ".join(card_label(h.card) for h, _ in rivals[:_NAMED])
    facts = f"{len(rivals)} competing products in the knowledge base"
    facts += f", closest: {named}." if named else "."

    items = engine.unflagged(h.item for h in products)
    # half of the room for the gaps; without any product there is nothing to compare them with
    gap_share = engine.unflagged(gap_evidence)[: engine.settings.max_evidence // 2] if items else []
    split = [*items[: engine.settings.max_evidence - len(gap_share)], *gap_share]
    scores = await asyncio.gather(
        engine.score(request, Dimension.COMPETITION, items, values.model_dump(), facts, deadline),
        engine.score(
            request, Dimension.DIFFERENTIATION, split, values.model_dump(), facts, deadline
        ),
    )
    return ScorerResponse(
        scores=list(scores),
        gate_inputs=PartialGateInputs(
            duplicate=_duplicate(engine, products[0] if products else None, text)
        ),
    )
