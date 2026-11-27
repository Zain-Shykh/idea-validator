"""The Validate workflow (architecture §6.2, WF-V1..V9): one function per step. Each returns the
next step, or None when the run waits for the student or is finished.

The verdict is only ever M11's reply (VE-1, WF-V9). Nothing a model wrote reaches a score, a
gate or the verdict: model text is limited to which questions to ask (M11 decides whether they
matter), which objections to check (M12 decides whether they hold) and the wording appended to
what-would-make-GO.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from common.cache_keys import step_key, verdict_key
from common.contracts.agents import (
    ApprovalPolicyRequest,
    ApprovalPolicyResponse,
    CandidateQuestion,
    CheckRequest,
    CheckResponse,
    ConcretizeRequest,
    ConcretizeResponse,
    DimensionChange,
    InputRailRequest,
    InputRailResponse,
    IntakeRequest,
    IntakeResponse,
    PartialGateInputs,
    ScanRequest,
    ScanResponse,
    ScorerRequest,
    ScorerResponse,
    SkepticRequest,
    SkepticResponse,
)
from common.contracts.core import (
    ApprovalRequest,
    Blocker,
    Claim,
    ClarifyQuestion,
    Dimension,
    DimensionScore,
    EvidenceItem,
    GateInputs,
    IdeaInput,
    Objection,
    PendingInput,
    RunEventType,
    RunState,
    TraceKind,
    Verdict,
    VerdictLabel,
    VerdictRequest,
    WhatWouldMakeGo,
)
from common.contracts.kb import (
    EvidenceRequest,
    EvidenceResponse,
    KbStats,
    SearchRequest,
    SearchResponse,
)
from common.contracts.ops import ApprovalEvent, OpsEvent, VerdictEvent
from common.contracts.profile import ApprovalCreate, VerdictStore
from common.contracts.scouts import ScoutRequest, ScoutResponse
from common.contracts.verdict import (
    ScoreChange,
    VerdictConfigResponse,
    WhatIfRequest,
    WhatIfResponse,
)
from common.http import ServiceError
from common.idea_text import CLARIFICATIONS, append, shorten
from common.injection import scan_text
from orchestrator.services import budget
from orchestrator.services.downstream import CHECK_TIMEOUT_S, PEERS
from orchestrator.services.run import (
    AGENT,
    RunContext,
    RunFailedError,
    stored,
)
from orchestrator.services.state_machine import BaseCheckpoint, Checkpoint, Step

# a Validate step's view of a run; the helpers Discover shares take any run
ValidateContext = RunContext[Checkpoint]

# which scorer produces which dimensions (§6.2, M9-1)
SCORERS: dict[str, tuple[Dimension, ...]] = {
    "demand": (Dimension.DEMAND_PAIN,),
    "competition": (Dimension.COMPETITION, Dimension.DIFFERENTIATION),
    "feasibility": (Dimension.FEASIBILITY, Dimension.LEARNING),
    "risk": (Dimension.RISK,),
}
# the only scorer that reads the profile, so the only one a profile change re-runs (M3-5)
PROFILE_SCORERS = frozenset({"feasibility"})
NO_MODEL = "none"
FAILED_PREFIX = "scoring failed"
MAX_SCORER_RETRIES = 2
MAX_QUESTIONS = 3
MAX_OBJECTIONS = 10
MAX_CONCRETIZE = 3
MAX_SUGGESTION_CHARS = 300
MAX_QUERY_CHARS = 2000
MAX_IDEA_CHARS = 5000
MAX_QUESTION_CHARS = 200
DEEP_SEARCH_ACTION = "deep_search"
CHECK_UNAVAILABLE_PREFIX = "claim check unavailable"


# --- INTAKE ------------------------------------------------------------------------------------


def cache_key(r: ValidateContext) -> str | None:
    """None when a version is unknown: then nothing can be cached safely (AVOID-2)."""
    cp = r.cp
    if cp.kb_version is None or cp.engine_version is None or cp.weights_version is None:
        return None
    return verdict_key(
        idea_text=f"{cp.idea.title}\n{cp.idea.text}",
        user_id=cp.user_id,
        profile_version=cp.profile.version,
        rubric_set=r.deps.rules.rubric_set,
        engine_version=cp.engine_version,
        weights_version=cp.weights_version,
        kb_version=cp.kb_version,
        prompt_versions=r.deps.rules.prompt_versions,
        scorer_model=r.deps.settings.scorer_model_id,
    )


async def _engine_versions(r: ValidateContext) -> None:
    try:
        reply: VerdictConfigResponse = await r.call(
            "verdict", "/config", method="GET", tool="verdict_config", agent="verdict",
            response_model=VerdictConfigResponse, summary=lambda c: c.versions.engine,
        )  # fmt: skip
        gate = reply.config["gates"]["evidence"]
        r.cp.min_sources, r.cp.min_platforms = int(gate["min_sources"]), int(gate["min_platforms"])
    except (ServiceError, KeyError, TypeError, ValueError) as exc:
        code = exc.code if isinstance(exc, ServiceError) else "INVALID_RESPONSE"
        raise RunFailedError(code, "the verdict service is not available") from exc
    r.cp.engine_version = reply.versions.engine
    r.cp.weights_version = reply.versions.profile_weights


async def _kb_version(r: ValidateContext) -> str | None:
    try:
        stats: KbStats = await r.call(
            "kb", "/stats", method="GET", tool="kb_stats", agent=AGENT,
            response_model=KbStats, summary=lambda s: s.kb_version,
        )  # fmt: skip
    except ServiceError:
        return None
    return stats.kb_version


async def _cached(r: ValidateContext) -> Verdict | None:
    key = cache_key(r)
    if key is None:
        return None
    try:
        found = await r.deps.store.cached_verdict(key)
    except ServiceError:
        r.decision("cache", {"verdict_cache": "unavailable"})
        return None
    verdict = found.verdict if found.hit else None
    r.cache_record("verdict_cache", verdict is not None)
    r.decision("cache", {"verdict_cache": "hit" if verdict else "miss"})
    return verdict


async def rail[C: BaseCheckpoint](r: RunContext[C], text: str) -> InputRailResponse:
    """The input rail fails closed: without it no model sees the text (M12-3)."""
    try:
        reply: InputRailResponse = await r.call(
            "critic", "/rail/input", body=InputRailRequest(text=text), tool="input_rail",
            agent="critic", response_model=InputRailResponse,
            summary=lambda x: {"allowed": x.allowed},
        )  # fmt: skip
    except ServiceError as exc:
        raise RunFailedError(exc.code, "the input check is not available") from exc
    return reply


async def intake(r: ValidateContext) -> Step | None:
    cp = r.cp
    await _engine_versions(r)
    cp.kb_version = await _kb_version(r)
    hit = await _cached(r)
    if hit is not None:
        cp.source_run_id = hit.run_id
        # the same decision for this run's ids; M11 made it, nothing is recomputed
        cp.verdict = hit.model_copy(
            update={
                "from_cache": True,
                "run_id": r.run_id,
                "idea_id": cp.idea_id,
                "version": cp.version,
            }
        )
        cp.cached = True
        await r.emit(
            RunEventType.CACHE_HIT, "Verdict found in the cache", agent=AGENT, step=cp.step,
            data={"tool": "verdict_cache"},
        )  # fmt: skip
        return Step.CARD

    await r.spend_steps(1)
    parts = {}
    for name, value in (("title", cp.idea.title), ("text", cp.idea.text)):
        reply = await rail(r, value)
        if not reply.allowed:
            raise RunFailedError(
                "INPUT_REJECTED", "the idea was refused by the input check",
                {"reasons": reply.reasons},
            )  # fmt: skip
        parts[name] = shorten(reply.redacted_text, 200 if name == "title" else MAX_IDEA_CHARS)
    cp.safe_idea = IdeaInput.model_validate(parts)

    if cp.parent_run_id is not None:
        return Step.EVIDENCE  # a parent cannot wait for answers, so nothing is asked
    await r.spend_steps(1)
    try:
        asked: IntakeResponse = await r.call(
            "ideation", "/intake", tool="intake", agent="intake",
            body=IntakeRequest(run_id=r.run_id, idea=cp.safe_idea, profile=cp.profile),
            response_model=IntakeResponse,
            summary=lambda x: [q.question_id for q in x.questions],
        )  # fmt: skip
    except ServiceError:
        cp.degrade("intake unavailable")
        return Step.EVIDENCE
    unique = {q.question_id: q for q in asked.questions[:MAX_QUESTIONS]}
    # a question's text ends up in the idea text, so it must not carry an order to a model
    cp.candidate_questions = [q for q in unique.values() if not scan_text(q.text)]
    return Step.EVIDENCE


# --- EVIDENCE and DEEP_SEARCH ------------------------------------------------------------------


def _agent_idea(r: ValidateContext) -> IdeaInput:
    assert r.cp.safe_idea is not None  # INTAKE sets it before any later step
    return r.cp.safe_idea


async def _search(r: ValidateContext) -> SearchResponse:
    idea = _agent_idea(r)
    request = SearchRequest(
        query=shorten(f"{idea.title}\n{idea.text}", MAX_QUERY_CHARS), k=r.deps.settings.search_k
    )
    key = None
    if r.cp.kb_version is not None and r.cp.deep_search != "done":
        key = step_key(
            "evidence",
            {"query": request.query, "k": request.k, "kb_version": r.cp.kb_version},
        )
        memo = await r.deps.memo.get(key, SearchResponse)
        if memo is not None:
            await r.cache_hit("search_kb", AGENT, "Evidence search reused", memo)
            return memo
    await r.spend_steps(1)
    try:
        found: SearchResponse = await r.call(
            "kb", "/search", body=request, tool="search_kb", agent=AGENT,
            response_model=SearchResponse,
            summary=lambda x: [i.evidence_id for i in x.results],
        )  # fmt: skip
    except ServiceError as exc:
        raise RunFailedError("KB_UNAVAILABLE", "the knowledge base could not be searched") from exc
    if key is not None:
        await r.deps.memo.put(key, found)
    return found


async def flagged_ids[C: BaseCheckpoint](r: RunContext[C], items: list[EvidenceItem]) -> set[str]:
    """Evidence that looks like an order to a model; it reaches no agent (M12-2, ETH-5)."""
    if not items:
        return set()
    try:
        reply: ScanResponse = await r.call(
            "critic", "/scan", body=ScanRequest(evidence=items), tool="scan", agent="critic",
            response_model=ScanResponse,
            summary=lambda x: [f.evidence_id for f in x.flagged],
        )  # fmt: skip
    except ServiceError:
        # the same rules, run here (common.injection); M12 counts blocked passages, M3 never
        r.decision("scan", {"critic": "unavailable", "rules": "local"})
        return {i.evidence_id for i in items if scan_text(i.quote, i.source)}
    return {f.evidence_id for f in reply.flagged}


async def evidence(r: ValidateContext) -> Step | None:
    cp = r.cp
    found = await _search(r)
    items = [EvidenceItem(**x.model_dump(exclude={"score", "card_name"})) for x in found.results]
    withheld = await flagged_ids(r, items)
    cp.evidence = [i for i in items if i.evidence_id not in withheld]
    cards = len({i.card_id for i in cp.evidence})
    sources = len({i.source for i in cp.evidence})
    thin = cards < cp.min_sources or sources < cp.min_platforms
    r.decision(
        "evidence",
        {"cards": cards, "sources": sources, "thin": thin, "withheld": len(withheld)},
    )
    if cp.deep_search == "approved":
        return Step.DEEP_SEARCH
    if not thin or cp.deep_search != "no":
        return Step.FEATURES
    return await _ask_for_deep_search(r)


async def _ask_for_deep_search(r: ValidateContext) -> Step | None:
    settings = r.deps.settings
    estimate = budget.deep_search_estimate(
        settings.deep_search_pages, settings.deep_search_page_cost_usd
    )
    r.cp.deep_search_pages = estimate.pages
    await r.sync_spend()
    left = r.remaining()
    try:
        policy: ApprovalPolicyResponse = await r.call(
            "critic", "/policy/approval", tool="approval_policy", agent="critic",
            body=ApprovalPolicyRequest(
                run_id=r.run_id, action=DEEP_SEARCH_ACTION, estimate=estimate, remaining=left
            ),
            response_model=ApprovalPolicyResponse,
            summary=lambda x: {"requires_approval": x.requires_approval},
        )  # fmt: skip
        needs, why = policy.requires_approval, policy.reason
    except ServiceError:
        needs, why = True, "the approval policy is not available"
    # a used-up step cap is also over budget, whatever M12 looked at
    needs = needs or left.max_steps < 1
    r.decision("approval_policy", {"action": DEEP_SEARCH_ACTION, "requires_approval": needs})
    if not needs:
        r.cp.deep_search = "approved"
        return Step.DEEP_SEARCH
    if r.cp.parent_run_id is not None:
        return _skip_deep_search(r)

    request = ApprovalRequest(
        # one deep search per run: asking again after a lost checkpoint is the same approval
        approval_id=f"appr_{r.run_id.removeprefix('run_')}",
        action=DEEP_SEARCH_ACTION,
        estimated_cost_usd=estimate.cost_usd,
        estimated_pages=estimate.pages,
    )
    await stored(r.deps.store.create_approval(ApprovalCreate(run_id=r.run_id, request=request)))
    r.cp.deep_search = "asked"
    r.pause(RunState.AWAITING_APPROVAL, PendingInput(kind="approval", approval=request))
    pending = ApprovalEvent(approval_id=request.approval_id, decision="pending")
    message = (
        f"A deep search will read up to {estimate.pages} pages and cost about "
        f"${estimate.cost_usd:.4f}, which is over this run's budget. Approve?"
    )
    data = {**request.model_dump(), "reason": why}

    async def announce() -> None:
        r.ops(OpsEvent.APPROVAL, pending)
        await r.emit(
            RunEventType.APPROVAL_REQUIRED, message, agent=AGENT, step=r.cp.step, data=data
        )

    r.after_save.append(announce)
    return None


def _skip_deep_search(r: ValidateContext) -> Step:
    """A child cannot ask for approval, so a search that needs it is not run (U4)."""
    r.cp.deep_search = "declined"
    r.cp.degrade("deep search needs approval, which a Discover run does not ask for")
    return Step.FEATURES


async def deep_search(r: ValidateContext) -> Step | None:
    cp = r.cp
    if cp.deep_search == "approved":
        pages = cp.deep_search_pages
        # tokens or cost may be used up although the search itself was within budget
        await r.spend_steps(1)
        if cp.parent_run_id is not None and pages > r.remaining().max_pages:
            # another child bought the pages after the policy looked
            _skip_deep_search(r)
            return Step.EVIDENCE
        cp.deep_search = "started"
        r.add_spend(pages=pages)
        # stored before the call: after a crash the search is not bought a second time
        await r.save()
        idea = _agent_idea(r)
        sources = [s.strip() for s in r.deps.settings.scout_sources.split(",") if s.strip()]
        try:
            reply: ScoutResponse = await r.call(
                "scouts", "/scout", tool="deep_search", agent="scouts", retry=False,
                body=ScoutRequest(
                    queries=[idea.title, idea.text[:200]], sources=sources,
                    page_budget=pages, run_id=r.run_id,
                ),
                response_model=ScoutResponse,
                summary=lambda x: {"fetched": x.fetched, "cards": x.ingested_card_ids},
            )  # fmt: skip
            r.add_spend(pages=reply.fetched - pages)
        except ServiceError:
            # the pages stay charged: some may have been fetched
            cp.degrade("deep search failed")
    else:
        cp.degrade("deep search was interrupted")
    cp.deep_search = "done"
    # the search may have added cards
    cp.kb_version = await _kb_version(r)
    return Step.EVIDENCE


# --- FEATURES and SCORING ----------------------------------------------------------------------


async def features(r: ValidateContext) -> Step | None:
    """Scorers compute their own features before their model call (B-006); the one number M3
    needs itself, in case the feasibility scorer fails, comes from the profile."""
    p = r.cp.profile
    r.cp.available_hours = p.hours_per_week * p.weeks_available * p.team_size
    return Step.SCORING


def _memo_key(r: ValidateContext, name: str, request: ScorerRequest) -> str | None:
    if r.cp.kb_version is None:
        return None
    rules = r.deps.rules
    prompts = ["scorer", *(["effort"] if name in PROFILE_SCORERS else [])]
    inputs: dict[str, Any] = {
        "idea": request.idea,
        "evidence": [[e.evidence_id, e.quote] for e in request.evidence],
        "rubrics": [rules.rubric_ids[d] for d in SCORERS[name]],
        "prompts": {p: rules.prompt_versions[p] for p in prompts},
        "scorer_model": r.deps.settings.scorer_model_id,
        "kb_version": r.cp.kb_version,
    }
    if name in PROFILE_SCORERS:
        inputs["profile"] = request.profile
    return step_key(f"score:{name}", inputs)


def _failed(score: DimensionScore) -> bool:
    return score.reason.startswith(FAILED_PREFIX)


def trustworthy(r: ValidateContext, reply: ScorerResponse) -> bool:
    """Whether a reply may be reused: no failure, no fallback model, a real effort estimate."""
    model = r.deps.settings.scorer_model_id
    return not any(
        _failed(s)
        or (s.score is not None and s.model != model)
        or s.features.get("effort_source") == "band_midpoint"
        for s in reply.scores
    )


def _abstain(r: ValidateContext, dimension: Dimension, reason: str) -> DimensionScore:
    return DimensionScore(
        dimension=dimension,
        score=None,
        confidence=0,
        evidence_ids=[],
        reason=reason,
        rubric_version=r.deps.rules.rubric_ids[dimension],
        model=NO_MODEL,
    )


def _right_shape(r: ValidateContext, name: str, reply: ScorerResponse) -> bool:
    got = sorted((s.dimension, s.rubric_version) for s in reply.scores)
    return got == sorted((d, r.deps.rules.rubric_ids[d]) for d in SCORERS[name])


async def _call_scorer(r: ValidateContext, name: str, request: ScorerRequest) -> ScorerResponse:
    """At most 3 attempts inside one limit (600 s, D-010). A timeout is never retried: the
    abandoned job still occupies the model. A scorer that still fails abstains (M9-4)."""
    code = "TIMEOUT"
    await r.tool_event(f"{name}_scorer", f"score_{name}")
    with r.deps.trace.step(
        AGENT, TraceKind.TOOL_CALL, tool=f"score_{name}",
        input=request.model_dump_json(), run_id=r.run_id,
    ) as step:  # fmt: skip
        try:
            async with asyncio.timeout(r.deps.settings.scorer_timeout_s):
                for attempt in range(MAX_SCORER_RETRIES + 1):
                    step.retries = attempt
                    if attempt:
                        await asyncio.sleep(r.deps.retry_wait_s * attempt)
                    try:
                        reply: ScorerResponse = await r.deps.peers.request(
                            "validators", "POST", f"/score/{name}", body=request,
                            response_model=ScorerResponse, retry=False,
                            idempotency_key=f"{r.run_id}:{name}",
                            limit_s=PEERS["validators"][2],
                        )  # fmt: skip
                    except ServiceError as exc:
                        code = exc.code
                        status = exc.status or 0
                        if code == "TIMEOUT" or (400 <= status < 500 and status != 429):
                            break
                        continue
                    if not _right_shape(r, name, reply):
                        code = "INVALID_RESPONSE"
                        continue
                    step.output = json.dumps(
                        [[s.dimension, s.score, s.model] for s in reply.scores]
                    )
                    step.reply = reply.model_dump_json(by_alias=True)
                    return reply
        except TimeoutError:
            code = "TIMEOUT"
        step.error = f"scorer failed: {code}"
    if code == "KB_UNAVAILABLE":
        # an outage is not the same as finding no evidence
        raise RunFailedError(code, "the knowledge base could not be searched")
    return ScorerResponse(
        scores=[_abstain(r, d, f"{FAILED_PREFIX}: validator {code}") for d in SCORERS[name]],
        gate_inputs=PartialGateInputs(),
    )


async def _only_known_evidence(
    r: ValidateContext, replies: dict[str, ScorerResponse]
) -> dict[str, ScorerResponse]:
    """No evidence, no claim: ids M6 does not know are stripped; a score or blocker with none
    left is dropped."""
    cited = sorted(
        {i for reply in replies.values() for s in reply.scores for i in s.evidence_ids}
        | {
            i
            for reply in replies.values()
            for b in reply.gate_inputs.blockers
            for i in b.evidence_ids
        }
    )
    if not cited:
        return replies
    try:
        found: EvidenceResponse = await r.call(
            "kb", "/evidence", body=EvidenceRequest(ids=cited), tool="kb_evidence", agent=AGENT,
            response_model=EvidenceResponse, summary=lambda x: [i.evidence_id for i in x.items],
        )  # fmt: skip
    except ServiceError as exc:
        raise RunFailedError("KB_UNAVAILABLE", "cited evidence could not be looked up") from exc
    known = {i.evidence_id for i in found.items} - set(found.missing)
    if set(cited) <= known:
        return replies
    r.decision("evidence_ids", {"unknown": sorted(set(cited) - known)})
    r.cp.degrade("a reply cited evidence the knowledge base does not have")
    checked = {}
    for name, reply in replies.items():
        scores = []
        for score in reply.scores:
            ids = [i for i in score.evidence_ids if i in known]
            if score.score is not None and not ids:
                scores.append(_abstain(r, score.dimension, "the cited evidence does not exist"))
            else:
                scores.append(score.model_copy(update={"evidence_ids": ids}))
        blockers = [
            Blocker(type=b.type, description=b.description, evidence_ids=ids)
            for b in reply.gate_inputs.blockers
            if (ids := [i for i in b.evidence_ids if i in known])
        ]
        gates = reply.gate_inputs.model_copy(update={"blockers": blockers})
        checked[name] = ScorerResponse(scores=scores, gate_inputs=gates)
    return checked


async def scoring(r: ValidateContext) -> Step | None:
    """Four scorers in parallel (M3-2, WF-V3). Only what changed is run again (M3-5)."""
    cp = r.cp
    todo: dict[str, tuple[ScorerRequest, str | None]] = {}
    for name in SCORERS:
        if name in cp.scorer_results:  # kept through a clarify round that does not affect it
            await r.cache_hit(f"score_{name}", f"{name}_scorer", f"{name} scores unchanged")
            continue
        request = ScorerRequest(
            run_id=r.run_id,
            idea=cp.scorer_ideas.setdefault(name, _agent_idea(r)),
            profile=cp.profile,
            evidence=cp.evidence,
        )
        key = _memo_key(r, name, request)
        memo = None if key is None else await r.deps.memo.get(key, ScorerResponse)
        if memo is not None:
            cp.scorer_results[name] = memo
            await r.cache_hit(f"score_{name}", f"{name}_scorer", f"{name} scores reused", memo)
            continue
        todo[name] = (request, key)

    if todo:
        await r.spend_steps(len(todo))
        tasks = [
            asyncio.create_task(_call_scorer(r, name, request))
            for name, (request, _) in todo.items()
        ]
        try:
            answered = await asyncio.gather(*tasks)
        except BaseException:  # one failed the run, or the run is cancelled: stop the others
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        raw = dict(zip(todo, answered, strict=True))
        for name, reply in (await _only_known_evidence(r, raw)).items():
            cp.scorer_results[name] = reply
            key = todo[name][1]
            # a stripped reply would come back from the memo without its degraded mark
            if key is not None and reply == raw[name] and trustworthy(r, reply):
                await r.deps.memo.put(key, reply)
        await r.sync_spend()
    return Step.PRELIM_VERDICT


# --- verdicts ----------------------------------------------------------------------------------


def merge_gate_inputs(results: dict[str, ScorerResponse], available_hours: int) -> GateInputs:
    """Each scorer fills only its own fields. A failed scorer leaves them empty, which M11
    reads as missing evidence or an unchecked gate, never as a pass it did not earn."""

    def part(name: str) -> PartialGateInputs:
        reply = results.get(name)
        return PartialGateInputs() if reply is None else reply.gate_inputs

    demand, feasibility = part("demand"), part("feasibility")
    hours = feasibility.available_hours
    return GateInputs(
        n_sources=demand.n_sources or 0,
        n_platforms=demand.n_platforms or 0,
        est_effort_hours=feasibility.est_effort_hours,
        available_hours=available_hours if hours is None else hours,
        blockers=part("risk").blockers,
        duplicate=part("competition").duplicate,
    )


def verdict_request(r: ValidateContext, objections: list[Objection]) -> VerdictRequest:
    cp = r.cp
    by_dimension = {s.dimension: s for reply in cp.scorer_results.values() for s in reply.scores}
    return VerdictRequest(
        idea_id=cp.idea_id,
        version=cp.version,
        run_id=r.run_id,
        goal=cp.profile.goal,
        profile=cp.profile,
        scores=[by_dimension[d] for d in Dimension],
        gate_inputs=merge_gate_inputs(cp.scorer_results, cp.available_hours),
        accepted_objections=objections,
        rubric_set=r.deps.rules.rubric_set,
        kb_version=cp.kb_version,
        prompt_versions=r.deps.rules.prompt_versions,
    )


async def _m11[T](
    r: ValidateContext,
    path: str,
    body: VerdictRequest,
    model: type[T],
    tool: str,
    summary: Callable[[T], object],
) -> T:
    try:
        reply: T = await r.call(
            "verdict", path, body=body, tool=tool, agent="verdict",
            response_model=model, summary=summary,
        )  # fmt: skip
    except ServiceError as exc:
        raise RunFailedError(exc.code, "the verdict service call failed") from exc
    return reply


async def _decide(r: ValidateContext, objections: list[Objection], tool: str) -> Verdict:
    """The verdict of the current scores, from M11 and only from M11 (VE-1). The request is
    kept with its original scores: M11 returns them after challenge penalties."""
    request = verdict_request(r, objections)
    verdict = await _m11(r, "/verdict", request, Verdict, tool, lambda v: [v.verdict, v.score])
    if (verdict.run_id, verdict.idea_id, verdict.version) != (
        r.run_id, request.idea_id, request.version
    ):  # fmt: skip
        raise RunFailedError("INVALID_RESPONSE", "the verdict is for another run")
    r.cp.verdict_request = request
    r.cp.verdict = verdict
    return verdict


async def _label_if(
    r: ValidateContext, deltas: dict[Dimension, float], current: Verdict
) -> VerdictLabel:
    """M11's verdict if the scores moved by ``deltas``. An unscored dimension cannot move."""
    request = r.cp.verdict_request
    assert request is not None
    scores = {s.dimension: s.score for s in request.scores}
    changes = []
    for dimension, delta in deltas.items():
        score = scores[dimension]
        if score is None:
            continue
        target = min(5.0, max(0.0, score + delta))
        if target != score:
            changes.append(ScoreChange(dimension=dimension, to=target))
    if not changes:
        return current.verdict
    body = WhatIfRequest.model_validate(
        {**request.model_dump(by_alias=True), "mode": "apply", "changes": changes}
    )
    reply = await _m11(
        r, "/whatif", body, WhatIfResponse, "whatif_apply",
        lambda x: None if x.verdict is None else x.verdict.verdict,
    )  # fmt: skip
    if reply.verdict is None:
        raise RunFailedError("INVALID_RESPONSE", "the verdict service returned no verdict")
    return reply.verdict.verdict


async def prelim_verdict(r: ValidateContext) -> Step | None:
    current = await _decide(r, [], "prelim_verdict")
    cp = r.cp
    if cp.clarified:  # at most one clarify round
        return Step.CHALLENGE
    flipping: list[CandidateQuestion] = []
    for question in cp.candidate_questions:
        first, second = [await _label_if(r, a.score_deltas, current) for a in question.answers]
        if first != second:
            flipping.append(question)
        r.decision(
            "clarify",
            {"question_id": question.question_id, "labels": [first, second],
             "ask": first != second},
        )  # fmt: skip
    cp.candidate_questions = flipping
    return Step.CLARIFY if flipping else Step.CHALLENGE


async def clarify(r: ValidateContext) -> Step | None:
    if r.cp.clarified:  # the answers are in
        return Step.SCORING
    questions = [
        ClarifyQuestion(question_id=q.question_id, text=q.text) for q in r.cp.candidate_questions
    ]
    r.pause(RunState.AWAITING_INPUT, PendingInput(kind="question", questions=questions))

    async def announce() -> None:
        for question in questions:
            await r.emit(
                RunEventType.QUESTION, question.text, agent="intake", step=r.cp.step,
                data=question.model_dump(),
            )  # fmt: skip

    r.after_save.append(announce)
    return None


def affected_scorers(questions: list[CandidateQuestion]) -> set[str]:
    touched = {d for q in questions for d in q.affects}
    touched |= {d for q in questions for a in q.answers for d in a.score_deltas}
    return {name for name, dimensions in SCORERS.items() if touched & set(dimensions)}


def with_clarifications(text: str, pairs: list[tuple[str, str]]) -> str:
    """The idea text with the answers appended (``common.idea_text``)."""
    # the first line is the question and only the question: shortening relies on it
    entries = [f"{' '.join(q.split())[:MAX_QUESTION_CHARS]}\n  {a}" for q, a in pairs]
    return append(text, CLARIFICATIONS, entries, MAX_IDEA_CHARS)


# --- CHALLENGE ---------------------------------------------------------------------------------


def usable_objections(
    objections: list[Objection], evidence: list[EvidenceItem], idea_id: str
) -> list[Objection]:
    """Objections that cite evidence the Skeptic was given (no evidence, no claim). M3 numbers
    them itself so that two can never share an id."""
    sent = {e.evidence_id for e in evidence}
    usable: list[Objection] = []
    for objection in objections:
        ids = [i for i in dict.fromkeys(objection.evidence_ids) if i in sent]
        if ids and not scan_text(objection.text):
            usable.append(
                objection.model_copy(
                    update={
                        "objection_id": f"obj_{len(usable) + 1}",
                        "idea_id": idea_id,
                        "evidence_ids": ids,
                    }
                )
            )
    return usable[:MAX_OBJECTIONS]


async def challenge(r: ValidateContext) -> Step | None:
    """The Skeptic objects, the Critic checks (WF-V6). Only supported objections reach M11."""
    cp = r.cp
    cp.objections = []
    if not cp.evidence:
        r.decision("challenge", {"raised": 0, "accepted": [], "why": "no evidence"})
        return Step.VERDICT
    await r.spend_steps(1)
    try:
        reply: SkepticResponse = await r.call(
            "ideation", "/skeptic", tool="skeptic", agent="skeptic",
            body=SkepticRequest(
                run_id=r.run_id, idea_id=cp.idea_id, idea=_agent_idea(r), evidence=cp.evidence
            ),
            response_model=SkepticResponse,
            summary=lambda x: [o.dimension for o in x.objections],
        )  # fmt: skip
    except ServiceError:
        cp.degrade("skeptic unavailable")
        return Step.VERDICT
    objections = usable_objections(reply.objections, cp.evidence, cp.idea_id)
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
            # unchecked objections apply no penalty (§12.5); no claim is removed
            cp.degrade("critic unavailable: objections not applied")
        else:
            if any(c.reason.startswith(CHECK_UNAVAILABLE_PREFIX) for c in checked.results):
                cp.degrade("critic could not check every objection")
            supported = {c.claim_id for c in checked.results if c.supported}
            cp.objections = [o for o in objections if o.objection_id in supported]
    r.decision(
        "challenge",
        {"raised": len(reply.objections), "accepted": [o.objection_id for o in cp.objections]},
    )
    return Step.VERDICT


# --- VERDICT, STABILITY, CARD ------------------------------------------------------------------


async def verdict(r: ValidateContext) -> Step | None:
    await _decide(r, r.cp.objections, "final_verdict")
    return Step.STABILITY


def with_suggestions(
    candidates: list[WhatWouldMakeGo], suggestions: dict[Dimension, str]
) -> list[WhatWouldMakeGo]:
    """The engine's candidates with model wording appended to the first one per dimension.
    Only the ``change`` text differs: scores and verdicts stay M11's."""
    left = dict(suggestions)
    worded = []
    for candidate in candidates:
        text = left.pop(candidate.dimension, None) if candidate.dimension else None
        if text:
            change = f"{candidate.change} (suggestion: {text[:MAX_SUGGESTION_CHARS]})"
            candidate = candidate.model_copy(update={"change": change})
        worded.append(candidate)
    return worded


async def stability(r: ValidateContext) -> Step | None:
    """What would make it GO (M11 search), put into words by M8 (WF-V7)."""
    request, current = r.cp.verdict_request, r.cp.verdict
    assert request is not None and current is not None
    body = WhatIfRequest.model_validate({**request.model_dump(by_alias=True), "mode": "search"})
    found = await _m11(
        r, "/whatif", body, WhatIfResponse, "whatif_search", lambda x: len(x.candidates)
    )
    changes: dict[Dimension, DimensionChange] = {}
    for c in found.candidates:
        if c.dimension and c.from_score is not None and c.to_score is not None:
            changes.setdefault(
                c.dimension,
                DimensionChange(
                    dimension=c.dimension, from_score=c.from_score, to_score=c.to_score
                ),
            )
    if not changes:
        return Step.CARD
    # wording is optional: over budget the card keeps the engine's own words
    await r.sync_spend()
    if r.budget_problems():
        r.cp.degrade("no budget left to word the changes")
        return Step.CARD
    r.add_spend(steps=1)
    try:
        reply: ConcretizeResponse = await r.call(
            "ideation", "/concretize", tool="concretize", agent="concretizer",
            body=ConcretizeRequest(
                run_id=r.run_id, idea=_agent_idea(r),
                changes=list(changes.values())[:MAX_CONCRETIZE],
            ),
            response_model=ConcretizeResponse,
            summary=lambda x: [c.dimension for c in x.changes],
        )  # fmt: skip
    except ServiceError:
        r.cp.degrade("concretizer unavailable")
        return Step.CARD
    suggestions = {
        c.dimension: c.change
        for c in reply.changes
        if c.dimension in changes and not scan_text(c.change)
    }
    r.cp.verdict = current.model_copy(
        update={"what_would_make_go": with_suggestions(current.what_would_make_go, suggestions)}
    )
    return Step.CARD


def claims_of(card: Verdict) -> list[Claim]:
    """What a student can challenge (U8, Z8): each scored dimension with its evidence."""
    return [
        Claim(
            claim_id=f"claim_{s.dimension.value}", dimension=s.dimension, text=s.reason,
            evidence_ids=s.evidence_ids,
        )
        for s in card.scores
        if s.score is not None
    ]  # fmt: skip


def not_cacheable(r: ValidateContext, card: Verdict) -> list[str]:
    """Why this card must not answer a later run (M2-4, AVOID-2)."""
    cp = r.cp
    reasons = list(cp.degraded)
    if cp.parent_run_id is not None:
        reasons.append("a Discover run asked no questions")
    if cp.clarified:
        reasons.append("answers were added during the run")
    if card.versions.engine != cp.engine_version:
        reasons.append("the verdict rules changed during the run")
    model = r.deps.settings.scorer_model_id
    if any(s.model == NO_MODEL or _failed(s) for s in card.scores):
        reasons.append("a dimension has no model score")
    if not all(trustworthy(r, reply) for reply in cp.scorer_results.values()) or any(
        s.score is not None and s.model != model for s in card.scores
    ):
        reasons.append("a score is a fallback or an estimate")
    return reasons


async def card(r: ValidateContext) -> Step | None:
    """Store the card, cache it if it may be reused, and finish (WF-V8)."""
    cp = r.cp
    result = cp.verdict
    assert result is not None
    await r.sync_spend()
    await r.tool_event(AGENT, "store_verdict")
    await stored(
        r.deps.store.store_verdict(
            VerdictStore(
                idea_version_id=cp.idea_version_id, verdict=result, claims=claims_of(result)
            ),
            key=f"{r.run_id}:card",
        )
    )
    key = cache_key(r)
    if not cp.cached and key is not None:
        reasons = not_cacheable(r, result)
        if not reasons:
            try:
                await r.deps.store.cache_verdict(key, result)
            except ServiceError:
                reasons = ["the cache is not available"]
        r.decision("cache", {"stored": not reasons, "why_not": reasons})
    r.set_status(RunState.COMPLETED)

    done = VerdictEvent(
        idea_id=result.idea_id, version=result.version, verdict=result.verdict,
        versions=result.versions,
    )  # fmt: skip

    async def announce() -> None:
        r.ops(OpsEvent.VERDICT, done)
        await r.emit(
            RunEventType.VERDICT, result.verdict.value, agent="verdict", step=Step.CARD,
            data={"verdict": result.model_dump(mode="json", by_alias=True)},
        )  # fmt: skip
        await r.emit(RunEventType.RUN_FINISHED, "Run completed")

    r.after_save.append(announce)
    return None


STEPS: dict[Step, Callable[[ValidateContext], Awaitable[Step | None]]] = {
    Step.INTAKE: intake,
    Step.EVIDENCE: evidence,
    Step.DEEP_SEARCH: deep_search,
    Step.FEATURES: features,
    Step.SCORING: scoring,
    Step.PRELIM_VERDICT: prelim_verdict,
    Step.CLARIFY: clarify,
    Step.CHALLENGE: challenge,
    Step.VERDICT: verdict,
    Step.STABILITY: stability,
    Step.CARD: card,
}
