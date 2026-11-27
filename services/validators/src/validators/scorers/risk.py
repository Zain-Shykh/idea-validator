"""Risk: keyword rules, platform dependency and the legal and safety blockers."""

from common.contracts.agents import PartialGateInputs, ScorerRequest, ScorerResponse
from common.contracts.base import ContractModel
from common.contracts.core import Blocker, Dimension
from validators.services import features as f
from validators.services.engine import Engine, Hit, idea_text

_MAX_BLOCKER_IDS = 3


class RiskFeatures(ContractModel):
    risk_hits: dict[str, list[str]]
    platform_dependency: list[str]
    regulated_domain: bool
    unconfirmed_blockers: list[str]


async def run(engine: Engine, request: ScorerRequest) -> ScorerResponse:
    deadline = engine.deadline()
    text = idea_text(request.idea)
    hits_by_category = f.risk_hits(text)
    rules = f.matched_rules(text)

    found: list[Hit] = await engine.retrieve(request, text)
    if hits_by_category:
        # a second search with the risk terms first, so a long idea cannot cut them off
        terms = " ".join(t for terms in hits_by_category.values() for t in terms)
        extra = await engine.retrieve(request, f"{terms}\n{text}")
        known = {h.card.id for h in found}
        found += [h for h in extra if h.card.id not in known]
    # cards on a matched topic first
    found.sort(key=lambda h: not f.mentions_risk(h.item.quote, hits_by_category))

    blockers: list[Blocker] = []
    blocker_hits: list[Hit] = []
    unconfirmed: list[str] = []
    for rule in rules:
        # the whole quote: no claim without a card on the same topic (M9-4)
        backing = [h for h in found if f.supports(rule, h.item.quote)][:_MAX_BLOCKER_IDS]
        if not backing:
            unconfirmed.append(rule.key)
            continue
        blockers.append(
            Blocker(
                type=rule.type,
                description=rule.description,
                evidence_ids=[h.item.evidence_id for h in backing],
            )
        )
        blocker_hits += backing

    values = RiskFeatures(
        risk_hits=hits_by_category,
        platform_dependency=f.platforms(text),
        regulated_domain=f.is_regulated(hits_by_category),
        unconfirmed_blockers=unconfirmed,
    ).model_dump()
    if unconfirmed:
        # abstaining keeps M11 from saying GO (the risk floor) without claiming a blocker
        score = engine.abstain(
            Dimension.RISK,
            f"possible blocker ({', '.join(unconfirmed)}) that no evidence confirms: not scored",
            values,
        )
    else:
        matched = ", ".join(hits_by_category) or "none"
        score = await engine.score(
            request,
            Dimension.RISK,
            [h.item for h in [*blocker_hits, *found]],
            values,
            f"Risk topics found by code: {matched}.",
            deadline,
        )
    return ScorerResponse(scores=[score], gate_inputs=PartialGateInputs(blockers=blockers))
