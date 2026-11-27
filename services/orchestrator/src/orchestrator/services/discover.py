"""The Discover workflow (architecture §6.4, WF-D1..D9): one function per step.

A bounded debate turns data gaps into ideas, then every survivor is validated by an ordinary
Validate run. Nothing here scores an idea or says a verdict: an idea is dropped by a rule in
code or by an objection the Critic found supported, and every verdict is a child run's, so it is
M11's (VE-1).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from common import context
from common.cache_keys import digest
from common.contracts.agents import (
    CheckRequest,
    CheckResponse,
    GapAnalyzeRequest,
    GapAnalyzeResponse,
    GapContext,
    IdeateRequest,
    IdeateResponse,
    NicheContext,
    SkepticRequest,
    SkepticResponse,
)
from common.contracts.analyst import Gap, GapsResponse, NicheListResponse, NicheMatchRequest
from common.contracts.core import (
    Claim,
    Dimension,
    EvidenceItem,
    GeneratedIdea,
    IdeaCard,
    IdeaInput,
    Objection,
    RunEventType,
    RunState,
    Workflow,
)
from common.contracts.kb import EvidenceRequest, EvidenceResponse, SearchRequest, SearchResponse
from common.contracts.profile import IdeaCreate, RunCreate, RunRecord
from common.contracts.verdict import VerdictConfigResponse
from common.http import ServiceError
from common.idea_rules import available_hours, effort_band, is_complaint, same_customer
from common.idea_text import shorten
from common.injection import scan_text
from orchestrator.services import validate
from orchestrator.services.downstream import CHECK_TIMEOUT_S
from orchestrator.services.results import ranked, run_result
from orchestrator.services.run import (
    AGENT,
    BudgetStoppedError,
    RunContext,
    RunFailedError,
    StoreUnavailableError,
    new_id,
    stored,
)
from orchestrator.services.state_machine import (
    MAX_ROUNDS,
    TERMINAL,
    Candidate,
    Checkpoint,
    Child,
    DiscoverCheckpoint,
    DiscoverStep,
)

DiscoverContext = RunContext[DiscoverCheckpoint]

MAX_NICHES = 3
MAX_GAPS = 12
MAX_GAPS_PER_NICHE = 4
MAX_GAP_EVIDENCE = 6
MAX_SKEPTIC_EVIDENCE = 20
# six ideas times five stays within one claim check (50)
MAX_OBJECTIONS_PER_IDEA = 5


# --- helpers -----------------------------------------------------------------------------------


def idea_input(idea: GeneratedIdea) -> IdeaInput:
    return IdeaInput(title=shorten(idea.title, 200), text=shorten(idea.text, 5000))


def _text_of(idea: GeneratedIdea) -> str:
    return f"{idea.title}\n{idea.text}"


def _unique(items: list[EvidenceItem]) -> list[EvidenceItem]:
    found: dict[str, EvidenceItem] = {}
    for item in items:
        found.setdefault(item.evidence_id, item)
    return list(found.values())


async def _known_evidence(r: DiscoverContext, ids: list[str]) -> dict[str, EvidenceItem]:
    """The ids M6 has, with their text."""
    if not ids:
        return {}
    try:
        reply: EvidenceResponse = await r.call(
            "kb", "/evidence", body=EvidenceRequest(ids=sorted(set(ids))), tool="kb_evidence",
            agent=AGENT, response_model=EvidenceResponse,
            summary=lambda x: [i.evidence_id for i in x.items],
        )  # fmt: skip
    except ServiceError as exc:
        raise RunFailedError("KB_UNAVAILABLE", "cited evidence could not be looked up") from exc
    return {i.evidence_id: i for i in reply.items if i.evidence_id not in reply.missing}


async def _usable(r: DiscoverContext, items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Without the passages that look like orders to a model (M12-2, ETH-5)."""
    withheld = await validate.flagged_ids(r, items)
    return [i for i in items if i.evidence_id not in withheld]


async def _drop(
    r: DiscoverContext,
    idea: GeneratedIdea,
    reason: str,
    message: str,
    *,
    objections: list[Objection] | None = None,
    evidence_ids: list[str] | None = None,
    card_id: str | None = None,
) -> None:
    """Tell the student what was dropped and why, with the evidence (WF-D6)."""
    objections = objections or []
    ids = list(
        dict.fromkeys([*(evidence_ids or []), *(i for o in objections for i in o.evidence_ids)])
    )
    data: dict[str, Any] = {
        "title": idea.title,
        "reason": reason,
        "objections": [o.model_dump(mode="json") for o in objections],
        "evidence_ids": ids,
    }
    if card_id is not None:
        data["card_id"] = card_id
    r.decision("drop", {"reason": reason, "evidence_ids": ids, "card_id": card_id})
    await r.emit(
        RunEventType.IDEA_DROPPED, f"Dropped '{idea.title}': {message}", agent=AGENT,
        step=r.cp.step, data=data,
    )  # fmt: skip


# --- REQUEST and NICHES ------------------------------------------------------------------------


async def request(r: DiscoverContext) -> DiscoverStep | None:
    cp = r.cp
    try:
        reply: VerdictConfigResponse = await r.call(
            "verdict", "/config", method="GET", tool="verdict_config", agent="verdict",
            response_model=VerdictConfigResponse, summary=lambda c: c.versions.engine,
        )  # fmt: skip
        cp.effort_margin = float(reply.config["gates"]["feasibility"]["effort_margin"])
    except (ServiceError, KeyError, TypeError, ValueError) as exc:
        code = exc.code if isinstance(exc, ServiceError) else "INVALID_RESPONSE"
        raise RunFailedError(code, "the verdict service is not available") from exc
    if cp.niche_wish and cp.niche_wish.strip():
        await r.spend_steps(1)
        checked = await validate.rail(r, cp.niche_wish)
        if not checked.allowed:
            raise RunFailedError(
                "INPUT_REJECTED", "the niche wish was refused by the input check",
                {"reasons": checked.reasons},
            )  # fmt: skip
        # only the redacted wish is kept and sent on
        cp.niche_wish = checked.redacted_text[:500]
    else:
        cp.niche_wish = None
    return DiscoverStep.NICHES


async def niches(r: DiscoverContext) -> DiscoverStep | None:
    cp = r.cp
    try:
        matched: NicheListResponse = await r.call(
            "analyst", "/niches/match", tool="match_niches", agent="analyst",
            body=NicheMatchRequest(profile=cp.profile, niche_wish=cp.niche_wish),
            response_model=NicheListResponse,
            summary=lambda x: [n.niche_id for n in x.niches],
        )  # fmt: skip
        chosen = matched.niches[:MAX_NICHES]
        gaps: list[Gap] = []
        for niche in chosen:
            found: GapsResponse = await r.call(
                "analyst", f"/gaps?niche_id={_query(niche.niche_id)}", method="GET",
                tool="niche_gaps", agent="analyst", response_model=GapsResponse,
                summary=lambda x: [g.gap_id for g in x.gaps],
            )  # fmt: skip
            best = sorted(found.gaps, key=lambda g: -g.score)[:MAX_GAPS_PER_NICHE]
            gaps += [g for g in best if g.niche_id == niche.niche_id]
    except ServiceError as exc:
        raise RunFailedError("ANALYST_UNAVAILABLE", "the niche analyst is unavailable") from exc
    gaps = gaps[:MAX_GAPS]
    known = await _known_evidence(r, [i for g in gaps for i in g.evidence_ids[:MAX_GAP_EVIDENCE]])
    usable = {i.evidence_id: i for i in await _usable(r, list(known.values()))}
    cp.niches = [
        NicheContext(niche_id=n.niche_id, name=n.name, trend_label=n.trend_label) for n in chosen
    ]
    cp.gaps = [
        GapContext(niche_id=g.niche_id, statement=g.statement, evidence=evidence)
        for g in gaps
        if (evidence := [usable[i] for i in g.evidence_ids[:MAX_GAP_EVIDENCE] if i in usable])
    ]
    r.decision("niches", {"niches": [n.niche_id for n in cp.niches], "gaps": len(cp.gaps)})
    if not cp.gaps:
        why = "no matching niche" if not chosen else "no gap with usable evidence"
        cp.note = f"No ideas: {why}"
        return DiscoverStep.RESULT
    return DiscoverStep.IDEATE


def _query(value: str) -> str:
    return quote(value, safe="")


# --- the debate --------------------------------------------------------------------------------


async def ideate(r: DiscoverContext) -> DiscoverStep | None:
    cp = r.cp
    await r.spend_steps(1)
    number = cp.round + 1
    try:
        reply: IdeateResponse = await r.call(
            "ideation", "/ideate", tool="ideate", agent="ideator",
            body=IdeateRequest(
                run_id=r.run_id, profile=cp.profile, niches=cp.niches, gaps=cp.gaps,
                round=number, prior_objections=cp.hints,
            ),
            response_model=IdeateResponse,
            summary=lambda x: {"ideas": len(x.ideas)},
        )  # fmt: skip
    except ServiceError as exc:
        if number == 1:
            raise RunFailedError("IDEATION_UNAVAILABLE", "no ideas could be made") from exc
        cp.degrade("a debate round failed")
        return DiscoverStep.VALIDATE_EACH
    cp.round = number
    cp.candidates = []
    flagged = 0
    for position, idea in enumerate(reply.ideas):
        # model text goes into prompts and onto the student's screen
        shown = (idea.title, idea.text, idea.target_user, idea.rationale)
        if any(scan_text(part) for part in shown):
            flagged += 1
            continue
        mark = digest(_text_of(idea))
        if mark in cp.seen:
            continue
        cp.seen.append(mark)
        cp.candidates.append(Candidate(key=f"r{number}i{position}", idea=idea))
    r.decision(
        "ideate",
        {"round": number, "returned": len(reply.ideas), "new": len(cp.candidates),
         "flagged": flagged},
    )  # fmt: skip
    return DiscoverStep.GAP_ANALYZE


async def gap_analyze(r: DiscoverContext) -> DiscoverStep | None:
    cp = r.cp
    if not cp.candidates:
        return DiscoverStep.SKEPTIC
    await r.spend_steps(1)
    try:
        reply: GapAnalyzeResponse = await r.call(
            "ideation", "/gap-analyze", tool="gap_analyze", agent="gap_analyst",
            body=GapAnalyzeRequest(run_id=r.run_id, ideas=[c.idea for c in cp.candidates]),
            response_model=GapAnalyzeResponse,
            summary=lambda x: [len(i.complaints) + len(i.missing_features) for i in x.ideas],
        )  # fmt: skip
    except ServiceError:
        cp.degrade("gap analyst unavailable")
        return DiscoverStep.SKEPTIC
    cited = {
        digest(_text_of(e.idea)): [
            i for claim in (*e.complaints, *e.missing_features) for i in claim.evidence_ids
        ]
        for e in reply.ideas
    }
    known = await _known_evidence(r, [i for ids in cited.values() for i in ids])
    usable = {i.evidence_id: i for i in await _usable(r, list(known.values()))}
    for candidate in cp.candidates:
        ids = dict.fromkeys(cited.get(digest(_text_of(candidate.idea)), []))
        candidate.found_evidence = [usable[i] for i in ids if i in usable]
    return DiscoverStep.SKEPTIC


async def _search_idea(r: DiscoverContext, idea: GeneratedIdea) -> SearchResponse:
    query = SearchRequest(query=shorten(_text_of(idea), 2000), k=r.deps.settings.search_k)
    try:
        found: SearchResponse = await r.call(
            "kb", "/search", body=query, tool="search_kb", agent=AGENT,
            response_model=SearchResponse,
            summary=lambda x: [i.evidence_id for i in x.results],
        )  # fmt: skip
    except ServiceError as exc:
        raise RunFailedError("KB_UNAVAILABLE", "the knowledge base could not be searched") from exc
    return found


async def _is_copy(r: DiscoverContext, idea: GeneratedIdea, found: SearchResponse) -> bool:
    """A renamed copy of a product: very similar and for the same customer (M8-5, AVOID-7)."""
    checked: set[str] = set()
    for hit in found.results:
        similar = r.deps.settings.duplicate_similarity <= hit.score <= 1  # >1: not a similarity
        if not similar or hit.card_id in checked:
            continue
        checked.add(hit.card_id)
        try:
            card: IdeaCard = await r.call(
                "kb", f"/cards/{_query(hit.card_id)}", method="GET", tool="kb_card", agent=AGENT,
                response_model=IdeaCard, summary=lambda c: c.id,
            )  # fmt: skip
        except ServiceError as exc:
            raise RunFailedError("KB_UNAVAILABLE", "a card could not be read") from exc
        if is_complaint(card):
            continue
        if same_customer(card.customer, f"{idea.target_user}\n{_text_of(idea)}"):
            await _drop(
                r, idea, "duplicate", f"{card.name} already does this for the same users",
                evidence_ids=[hit.evidence_id], card_id=card.id,
            )  # fmt: skip
            return True
    return False


async def _too_big(r: DiscoverContext, candidate: Candidate) -> bool:
    """Arithmetic, not an opinion: it cites no evidence and never reaches M11 (Z8-2)."""
    idea = candidate.idea
    low = effort_band(_text_of(idea))[0]
    hours = available_hours(r.cp.profile)
    limit = r.cp.effort_margin * hours
    if low <= limit:
        return False
    why = f"it needs at least {low} hours; the student has {hours} hours (limit {limit:.0f} hours)"
    objection = Objection(
        objection_id=f"{candidate.key}_hours", idea_id=candidate.key,
        dimension=Dimension.FEASIBILITY, text=why, evidence_ids=[], fatal=True,
    )  # fmt: skip
    await _drop(r, idea, "feasibility", why, objections=[objection])
    return True


async def _code_filters(r: DiscoverContext) -> list[tuple[Candidate, list[EvidenceItem]]]:
    """Cheapest first: gap evidence, renamed copy, hours. Returns the ideas still standing
    with what the Skeptic may see about each."""
    cp = r.cp
    known = await _known_evidence(r, [i for c in cp.candidates for i in c.idea.gap_evidence_ids])
    usable = {i.evidence_id: i for i in await _usable(r, list(known.values()))}
    standing = []
    for candidate in cp.candidates:
        ids = [i for i in candidate.idea.gap_evidence_ids if i in known]
        if not ids:
            await _drop(
                r, candidate.idea, "gap_evidence",
                "the knowledge base does not have the evidence it cites",
            )  # fmt: skip
            continue
        candidate.idea = candidate.idea.model_copy(update={"gap_evidence_ids": ids})
        candidate.gap_evidence = [usable[i] for i in ids if i in usable]
        found = await _search_idea(r, candidate.idea)
        if await _is_copy(r, candidate.idea, found) or await _too_big(r, candidate):
            continue
        results = [
            EvidenceItem(**x.model_dump(exclude={"score", "card_name"})) for x in found.results
        ]
        evidence = _unique(
            [*candidate.gap_evidence, *candidate.found_evidence, *await _usable(r, results)]
        )
        standing.append((candidate, evidence[:MAX_SKEPTIC_EVIDENCE]))
    return standing


async def skeptic(r: DiscoverContext) -> DiscoverStep | None:
    cp = r.cp
    if not cp.candidates:
        return DiscoverStep.CRITIC
    standing = await _code_filters(r)
    for candidate, evidence in standing:
        candidate.objections = []
        if not evidence:
            continue
        await r.spend_steps(1)
        try:
            reply: SkepticResponse = await r.call(
                "ideation", "/skeptic", tool="skeptic", agent="skeptic",
                body=SkepticRequest(
                    run_id=r.run_id, idea_id=candidate.key, idea=idea_input(candidate.idea),
                    evidence=evidence,
                ),
                response_model=SkepticResponse,
                summary=lambda x: [o.dimension for o in x.objections],
            )  # fmt: skip
        except ServiceError:
            cp.degrade("skeptic unavailable for an idea")
            continue
        usable = validate.usable_objections(reply.objections, evidence, candidate.key)
        # one claim check holds the objections of all ideas, so their ids must differ
        candidate.objections = [
            o.model_copy(update={"objection_id": f"{candidate.key}_{o.objection_id}"})
            for o in usable[:MAX_OBJECTIONS_PER_IDEA]
        ]
    cp.candidates = [candidate for candidate, _ in standing]
    return DiscoverStep.CRITIC


async def critic(r: DiscoverContext) -> DiscoverStep | None:
    """Only objections the Critic finds supported by their evidence stay (WF-D5)."""
    cp = r.cp
    objections = [o for c in cp.candidates for o in c.objections]
    supported: set[str] = set()
    if objections:
        await r.spend_steps(1)
        claims = [
            Claim(
                claim_id=o.objection_id, dimension=o.dimension, text=o.text,
                evidence_ids=o.evidence_ids,
            )
            for o in objections
        ]  # fmt: skip
        try:
            checked: CheckResponse = await r.call(
                "critic", "/check", body=CheckRequest(claims=claims), tool="claim_check",
                agent="critic", response_model=CheckResponse,
                summary=lambda x: [[c.claim_id, c.verdict] for c in x.results],
                timeout_s=CHECK_TIMEOUT_S, limit_s=CHECK_TIMEOUT_S,
            )  # fmt: skip
        except ServiceError:
            cp.degrade("critic unavailable: objections not applied")
        else:
            if any(c.reason.startswith(validate.CHECK_UNAVAILABLE_PREFIX) for c in checked.results):
                cp.degrade("critic could not check every objection")
            supported = {c.claim_id for c in checked.results if c.supported}
    for candidate in cp.candidates:
        candidate.objections = [o for o in candidate.objections if o.objection_id in supported]
    r.decision("critic", {"raised": len(objections), "supported": len(supported)})
    return DiscoverStep.FILTER


async def filter_(r: DiscoverContext) -> DiscoverStep | None:
    """An idea is dropped only for a fatal objection the Critic supports (M8-4)."""
    cp = r.cp
    cp.hints = []
    for candidate in cp.candidates:
        cp.hints += [o for o in candidate.objections if not o.fatal]
        fatal = [o for o in candidate.objections if o.fatal]
        if fatal:
            await _drop(r, candidate.idea, "defeated", fatal[0].text, objections=fatal)
            continue
        title = candidate.idea.title.casefold()
        # a later revision replaces the idea it revises
        cp.survivors = [s for s in cp.survivors if s.idea.title.casefold() != title]
        cp.survivors.append(candidate)
    cp.candidates = []
    enough = len(cp.survivors) >= cp.target
    r.decision("filter", {"round": cp.round, "survivors": len(cp.survivors), "enough": enough})
    if cp.round < MAX_ROUNDS and not enough:
        return DiscoverStep.IDEATE
    return DiscoverStep.VALIDATE_EACH


# --- VALIDATE_EACH -----------------------------------------------------------------------------


async def _record_of(r: DiscoverContext, child: Child) -> RunRecord | None:
    """The child run in M2, or None when it was never created or never checkpointed."""
    try:
        record = await r.deps.store.get_run(child.run_id)
    except ServiceError as exc:
        if exc.status == 404:
            return None
        raise StoreUnavailableError(exc.summary) from None
    return record if record.state else None


async def _create_child(r: DiscoverContext, child: Child) -> RunContext[Checkpoint]:
    """The survivor as a ``generated`` idea in M2 and a queued child run for it. Every call
    is idempotent, so a crash in between is repeated safely."""
    cp, idea = r.cp, child.idea
    content = idea_input(idea)
    saved = await stored(
        r.deps.store.create_idea(
            IdeaCreate(
                user_id=cp.user_id,
                title=content.title,
                text=content.text,
                source="generated",
                niche_id=idea.niche_id,
            ),
            key=f"{r.run_id}:{child.run_id}",
        )
    )
    if not saved.versions or saved.user_id != cp.user_id:
        raise StoreUnavailableError("M2 returned an idea without a version or of another user")
    version = saved.versions[0]
    budget = r.deps.rules.budgets[Workflow.VALIDATE]
    now = r.deps.clock()
    state = Checkpoint(
        workflow=Workflow.VALIDATE, user_id=cp.user_id, created_at=now, updated_at=now,
        profile=cp.profile, budget=budget, parent_run_id=r.run_id, idea_id=saved.id,
        idea_version_id=version.id, version=version.version, idea=content,
    )  # fmt: skip
    await stored(
        r.deps.store.create_run(
            RunCreate(
                run_id=child.run_id,
                user_id=cp.user_id,
                workflow=Workflow.VALIDATE,
                profile_version=cp.profile.version,
                budget=budget,
                parent_run_id=r.run_id,
            )
        )
    )
    run = RunContext(child.run_id, state, r.deps, parent=(r, child))
    await run.save()
    await run.emit(RunEventType.RUN_STARTED, f"Validate run started: {content.title}")
    return run


def _collect(child: Child, run: RunContext[Checkpoint]) -> None:
    cp = run.cp
    child.spend = cp.spend
    if cp.status is RunState.COMPLETED and cp.verdict is not None:
        child.outcome, child.verdict = "done", cp.verdict
    elif cp.status is RunState.BUDGET_STOPPED:
        child.outcome = "budget"
    else:
        child.outcome = "failed"


async def _run_child(r: DiscoverContext, child: Child, run: RunContext[Checkpoint] | None) -> None:
    # this task has its own copy of the context: records and calls carry the child's run id
    context.run_id_var.set(child.run_id)
    context.parent_step_var.set(None)
    if run is None:
        if r.budget_problems():
            child.outcome = "budget"
            return
        run = await _create_child(r, child)
    if run.cp.status not in TERMINAL:
        assert r.drive is not None
        await r.drive(run)
        if run.stored_status not in TERMINAL:
            # its end could not be stored: stop here, both runs stay resumable
            raise StoreUnavailableError(f"child {child.run_id} did not finish")
    _collect(child, run)
    r.cp.total_spend()


async def validate_each(r: DiscoverContext) -> DiscoverStep | None:
    """Each survivor goes through the same Validate workflow as any idea (WF-D8)."""
    cp = r.cp
    if not cp.children:
        # the least contested first, so that a short budget goes to them
        best = sorted(cp.survivors, key=lambda s: len(s.objections))[: cp.target]
        if not best:
            cp.note = cp.note or "No ideas: none survived the debate"
            return DiscoverStep.RESULT
        cp.children = [Child(run_id=new_id("run"), idea=s.idea) for s in best]
        # stored before any child exists, so a resumed run cannot make a second one
        await r.save()
    await r.sync_spend()
    # what existing children spent counts before any new one may start
    runs: dict[str, RunContext[Checkpoint]] = {}
    for child in cp.children:
        record = await _record_of(r, child)
        if record is None:
            continue
        state = Checkpoint.model_validate(record.state)
        child.spend = state.spend
        runs[child.run_id] = RunContext(child.run_id, state, r.deps, parent=(r, child))
    cp.total_spend()
    slots = asyncio.Semaphore(r.deps.settings.discover_concurrency)

    async def one(child: Child) -> None:
        async with slots:
            await _run_child(r, child, runs.get(child.run_id))
        await r.save()

    tasks = [asyncio.create_task(one(c)) for c in cp.children if c.outcome == "pending"]
    try:
        await asyncio.gather(*tasks)
    except BaseException:  # the store is gone, or the run is cancelled: stop the others
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return DiscoverStep.RESULT


# --- RESULT ------------------------------------------------------------------------------------


async def result(r: DiscoverContext) -> DiscoverStep | None:
    cp = r.cp
    await r.sync_spend()
    cards = ranked(c.verdict for c in cp.children if c.verdict is not None)
    left_out = [c for c in cp.children if c.verdict is None]
    if left_out:
        data = {
            "code": "IDEAS_NOT_VALIDATED",
            "ideas": [{"title": c.idea.title, "why": c.outcome} for c in left_out],
        }
        await r.emit(
            RunEventType.ERROR,
            "Not validated: " + ", ".join(c.idea.title for c in left_out),
            agent=AGENT, step=cp.step, data=data,
        )  # fmt: skip
    if left_out and not cards:
        if any(c.outcome == "budget" for c in left_out):
            raise BudgetStoppedError(["no idea could be validated within the budget"])
        raise RunFailedError("VALIDATION_FAILED", "no idea could be validated")
    cp.result = run_result(*cards)
    r.set_status(RunState.COMPLETED)
    message = cp.note if not cards and cp.note else "Run completed"
    r.decision("result", {"ranked": [[c.idea_id, c.verdict] for c in cards]})

    async def announce() -> None:
        await r.emit(RunEventType.RUN_FINISHED, message)

    r.after_save.append(announce)
    return None


STEPS: dict[DiscoverStep, Callable[[DiscoverContext], Awaitable[DiscoverStep | None]]] = {
    DiscoverStep.REQUEST: request,
    DiscoverStep.NICHES: niches,
    DiscoverStep.IDEATE: ideate,
    DiscoverStep.GAP_ANALYZE: gap_analyze,
    DiscoverStep.SKEPTIC: skeptic,
    DiscoverStep.CRITIC: critic,
    DiscoverStep.FILTER: filter_,
    DiscoverStep.VALIDATE_EACH: validate_each,
    DiscoverStep.RESULT: result,
}
