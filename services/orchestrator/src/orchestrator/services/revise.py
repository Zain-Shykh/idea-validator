"""Challenge a claim (U8) and "what would make this GO" (U7), architecture §6.6.

Both start from the engine input of a stored verdict. A challenge may only take evidence away
from M11's input and a what-if only changes the idea's text: every new verdict is M11's reply
(VE-1, VE-12). No score is edited or copied.
"""

import re
from collections.abc import Awaitable
from dataclasses import dataclass

from common import context
from common.cache_keys import digest
from common.contracts.agents import (
    CheckRequest,
    CheckResponse,
    ConcretizeRequest,
    ConcretizeResponse,
    DimensionChange,
)
from common.contracts.core import (
    Claim,
    Dimension,
    DimensionScore,
    IdeaInput,
    RunState,
    Spend,
    Verdict,
    VerdictRequest,
    WhatWouldMakeGo,
    Workflow,
)
from common.contracts.ops import OpsEvent, VerdictEvent
from common.contracts.orchestrator import (
    ChallengeRequest,
    ChallengeResponse,
    WhatIfApplyResponse,
    WhatIfProposal,
    WhatIfProposalsResponse,
)
from common.contracts.profile import (
    Idea,
    IdeaVersion,
    IdeaVersionCreate,
    RunCreate,
    RunRecord,
    VerdictStore,
)
from common.contracts.verdict import WhatIfRequest, WhatIfResponse
from common.http import ServiceError
from common.idea_text import CHANGES, append
from common.injection import scan_text
from common.service import ApiError
from orchestrator.services import validate
from orchestrator.services.downstream import CHECK_TIMEOUT_S
from orchestrator.services.run import RunContext, StoreUnavailableError
from orchestrator.services.runner import Runner
from orchestrator.services.state_machine import Checkpoint, Step

# what M9's code puts around a reason: bookkeeping, not a claim about the evidence
_PREFIX = re.compile(r"^[a-z_]+ [0-9.]+/5 from \S+\. ")
_CITES = re.compile(r"\s*Cites [^.]*\.$")
REMOVED_REASON = "removed after a challenge: the cited evidence does not support the claim"
MAX_CHANGE_CHARS = 500


def claim_text(reason: str) -> str:
    return _CITES.sub("", _PREFIX.sub("", reason)).strip() or reason


def _unavailable(what: str) -> ApiError:
    return ApiError(503, "DEPENDENCY_UNAVAILABLE", f"{what} is unavailable")


def _not_found(what: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", what)


def _derived_id(*parts: object) -> str:
    """The same request makes the same run, so a repeat changes nothing."""
    return "run_" + digest(":".join(str(p) for p in parts))[:26]


async def _from[T](what: str, call: Awaitable[T], missing: str | None = None) -> T:
    try:
        return await call
    except ServiceError as exc:
        if exc.status == 404 and missing is not None:
            raise _not_found(missing) from exc
        raise _unavailable(what) from exc


@dataclass
class Judged:
    """A user's idea version, its stored verdict and what M11 was given to reach it."""

    idea: Idea
    version: IdeaVersion
    stored: VerdictStore
    source: Checkpoint
    request: VerdictRequest


async def _owned(
    runner: Runner, idea_id: str, user_id: str, number: int
) -> tuple[Idea, IdeaVersion]:
    """Another user's idea looks like no idea at all."""
    idea = await _from("the idea store", runner.deps.store.idea(idea_id), missing="unknown idea")
    if idea.user_id != user_id or idea.id != idea_id:
        raise _not_found("unknown idea")
    version = next((v for v in idea.versions if v.version == number), None)
    if version is None:
        raise _not_found("unknown idea version")
    return idea, version


async def _state_of(runner: Runner, run_id: str, user_id: str) -> Checkpoint:
    record = await _from("the run store", runner.deps.store.get_run(run_id))
    try:
        state = Checkpoint.model_validate(record.state)
    except ValueError as exc:
        raise ApiError(409, "CONFLICT", "the stored run cannot be read") from exc
    if state.user_id != user_id:
        raise ApiError(409, "CONFLICT", "the stored run belongs to someone else")
    return state


async def _judged(runner: Runner, idea_id: str, user_id: str, number: int) -> Judged:
    idea, version = await _owned(runner, idea_id, user_id, number)
    stored = await _from(
        "the idea store",
        runner.deps.store.stored_verdict(idea_id, number),
        missing="this version has no verdict",
    )
    card = stored.verdict
    if (card.idea_id, card.version) != (idea_id, number):
        raise _unavailable("the idea store")
    source = await _state_of(runner, card.run_id, user_id)
    if source.source_run_id is not None:  # a cache hit: the input is where it was computed
        source = await _state_of(runner, source.source_run_id, user_id)
    if source.verdict_request is None or source.safe_idea is None:
        raise ApiError(409, "CONFLICT", "the stored run lacks the engine input of this verdict")
    request = source.verdict_request.model_copy(
        update={"idea_id": idea_id, "version": number, "run_id": card.run_id}
    )
    return Judged(idea, version, stored, source, request)


def _context(runner: Runner, run_id: str, state: Checkpoint) -> RunContext[Checkpoint]:
    return RunContext(run_id, state, runner.deps)


async def _existing(runner: Runner, run_id: str) -> RunRecord | None:
    try:
        record = await runner.deps.store.get_run(run_id)
    except ServiceError as exc:
        if exc.status == 404:
            return None
        raise _unavailable("the run store") from exc
    return record if record.state else None


# --- challenge ---------------------------------------------------------------------------------


def without_evidence(scores: list[DimensionScore], claim: Claim) -> list[DimensionScore]:
    """M11's input once a claim is removed: its evidence ids go, and a dimension with none
    left abstains."""
    changed = []
    for score in scores:
        if score.dimension is claim.dimension and score.score is not None:
            left = [i for i in score.evidence_ids if i not in claim.evidence_ids]
            if left:
                score = score.model_copy(update={"evidence_ids": left})
            else:
                score = DimensionScore(
                    dimension=score.dimension, score=None, confidence=0, evidence_ids=[],
                    reason=REMOVED_REASON, rubric_version=score.rubric_version,
                    model=validate.NO_MODEL,
                )  # fmt: skip
        changed.append(score)
    return changed


async def challenge(
    runner: Runner, idea_id: str, user_id: str, body: ChallengeRequest
) -> ChallengeResponse:
    async with runner.lock(f"idea:{idea_id}"):
        found = await _judged(runner, idea_id, user_id, body.version)
        claim = next((c for c in found.stored.claims if c.claim_id == body.claim_id), None)
        if claim is None or claim.dimension not in set(Dimension):
            raise _not_found("unknown claim")
        run_id = _derived_id("challenge", idea_id, body.version, claim.claim_id)
        done = await _existing(runner, run_id)
        if done is not None:
            state = Checkpoint.model_validate(done.state)
            if state.verdict is not None and state.challenge_rationale is not None:
                return ChallengeResponse(
                    outcome="removed",
                    rationale=state.challenge_rationale,
                    verdict=state.verdict,
                )
        token = context.run_id_var.set(run_id)
        try:
            return await _check(runner, run_id, found, claim)
        except StoreUnavailableError as exc:
            raise _unavailable("the run store") from exc
        finally:
            context.run_id_var.reset(token)


async def _check(runner: Runner, run_id: str, found: Judged, claim: Claim) -> ChallengeResponse:
    now = runner.deps.clock()
    state = found.source.model_copy(deep=True)
    state.status, state.step = RunState.COMPLETED, Step.CARD
    state.created_at = state.updated_at = now
    state.parent_run_id, state.source_run_id = None, None
    # the source run's spend stays on the source run
    state.spend = Spend()
    state.idea_id = found.idea.id
    state.idea = IdeaInput(title=found.idea.title, text=found.version.text)
    r = _context(runner, run_id, state)
    sent = claim.model_copy(update={"text": claim_text(claim.text)})
    try:
        checked: CheckResponse = await r.call(
            "critic", "/check", body=CheckRequest(claims=[sent], strict=True),
            tool="claim_check", agent="critic", response_model=CheckResponse,
            summary=lambda x: [[c.claim_id, c.verdict] for c in x.results],
            timeout_s=CHECK_TIMEOUT_S, limit_s=CHECK_TIMEOUT_S,
        )  # fmt: skip
    except ServiceError as exc:
        raise _unavailable("the claim check") from exc
    result = next((c for c in checked.results if c.claim_id == claim.claim_id), None)
    if result is None or result.reason.startswith(validate.CHECK_UNAVAILABLE_PREFIX):
        # "could not check" is not "unsupported": nothing changes (§12.5)
        raise _unavailable("the claim check")
    r.decision("challenge_claim", {"claim_id": claim.claim_id, "result": result.verdict})
    if result.supported:
        return ChallengeResponse(
            outcome="defended", rationale=result.reason, verdict=found.stored.verdict
        )

    version = await _from(
        "the idea store",
        runner.deps.store.add_version(
            found.idea.id,
            IdeaVersionCreate(
                text=found.version.text, origin="challenge", parent_version_id=found.version.id
            ),
            key=f"{run_id}:version",
        ),
    )
    request = found.request.model_copy(
        update={
            "version": version.version,
            "run_id": run_id,
            "scores": without_evidence(found.request.scores, claim),
        }
    )
    try:
        card: Verdict = await r.call(
            "verdict", "/verdict", body=request, tool="final_verdict", agent="verdict",
            response_model=Verdict, summary=lambda v: [v.verdict, v.score],
        )  # fmt: skip
    except ServiceError as exc:
        raise _unavailable("the verdict service") from exc
    if (card.run_id, card.idea_id, card.version) != (run_id, found.idea.id, version.version):
        raise _unavailable("the verdict service")

    state.idea_version_id, state.version = version.id, version.version
    state.verdict_request, state.verdict = request, card
    state.objections = list(request.accepted_objections)
    state.cached = False
    state.challenge_rationale = result.reason
    await _from(
        "the run store",
        runner.deps.store.create_run(
            RunCreate(
                run_id=run_id,
                user_id=state.user_id,
                workflow=Workflow.VALIDATE,
                profile_version=state.profile.version,
                budget=state.budget,
            )
        ),
    )
    await _from(
        "the idea store",
        runner.deps.store.store_verdict(
            VerdictStore(idea_version_id=version.id, verdict=card, claims=validate.claims_of(card)),
            key=f"{run_id}:card",
        ),
    )
    # last: a completed run with a rationale is what a repeat of this challenge returns
    await r.save()
    r.ops(
        OpsEvent.VERDICT,
        VerdictEvent(
            idea_id=card.idea_id,
            version=card.version,
            verdict=card.verdict,
            versions=card.versions,
        ),
    )
    return ChallengeResponse(outcome="removed", rationale=result.reason, verdict=card)


# --- what would make this GO -------------------------------------------------------------------


def _proposal_key(idea_id: str, version: int, proposal_id: str) -> str:
    return f"orch:whatif:{idea_id}:{version}:{proposal_id}"


def _one_per_dimension(candidates: list[WhatWouldMakeGo]) -> list[WhatWouldMakeGo]:
    """M11 returns them best first."""
    best: dict[Dimension, WhatWouldMakeGo] = {}
    for c in candidates:
        if c.dimension and c.from_score is not None and c.to_score is not None:
            best.setdefault(c.dimension, c)
    return list(best.values())[: validate.MAX_CONCRETIZE]


async def proposals(
    runner: Runner, idea_id: str, user_id: str, number: int
) -> WhatIfProposalsResponse:
    found = await _judged(runner, idea_id, user_id, number)
    run_id = "whatif_" + digest(f"{idea_id}:{number}")[:24]
    token = context.run_id_var.set(run_id)
    try:
        r = _context(runner, run_id, found.source.model_copy(deep=True))
        search = WhatIfRequest.model_validate(
            {**found.request.model_dump(by_alias=True), "mode": "search"}
        )
        try:
            reply: WhatIfResponse = await r.call(
                "verdict", "/whatif", body=search, tool="whatif_search", agent="verdict",
                response_model=WhatIfResponse, summary=lambda x: len(x.candidates),
            )  # fmt: skip
        except ServiceError as exc:
            raise _unavailable("the verdict service") from exc
        candidates = _one_per_dimension(reply.candidates)
        if not candidates:
            return WhatIfProposalsResponse(proposals=[])
        changes = [
            DimensionChange(dimension=c.dimension, from_score=c.from_score, to_score=c.to_score)
            for c in candidates
            if c.dimension and c.from_score is not None and c.to_score is not None
        ]
        idea: IdeaInput | None = found.source.safe_idea
        assert idea is not None  # _judged checked it
        try:
            worded: ConcretizeResponse = await r.call(
                "ideation", "/concretize", tool="concretize", agent="concretizer",
                body=ConcretizeRequest(run_id=run_id, idea=idea, changes=changes),
                response_model=ConcretizeResponse,
                summary=lambda x: [c.dimension for c in x.changes],
            )  # fmt: skip
        except ServiceError as exc:
            raise _unavailable("the concretizer") from exc
    finally:
        context.run_id_var.reset(token)
    # a change is appended to the idea text, so it must not carry an order to a model
    words = {
        c.dimension: c.change[:MAX_CHANGE_CHARS] for c in worded.changes if not scan_text(c.change)
    }
    listed = []
    for c in candidates:
        if c.dimension in words:
            marks = f"{idea_id}:{number}:{c.dimension}:{c.from_score}:{c.to_score}"
            proposal = WhatIfProposal(
                proposal_id="wi_" + digest(marks)[:16],
                concrete_change=words[c.dimension],
                candidate=c,
            )
            await runner.deps.proposals.put(
                _proposal_key(idea_id, number, proposal.proposal_id), proposal
            )
            listed.append(proposal)
    return WhatIfProposalsResponse(proposals=listed)


async def apply(
    runner: Runner, idea_id: str, user_id: str, number: int, proposal_id: str
) -> WhatIfApplyResponse:
    async with runner.lock(f"idea:{idea_id}"):
        idea, version = await _owned(runner, idea_id, user_id, number)
        run_id = _derived_id("whatif", idea_id, number, proposal_id)
        done = await _existing(runner, run_id)
        if done is not None:
            state = Checkpoint.model_validate(done.state)
            await runner.ensure_started(run_id, "Validate run started")
            return WhatIfApplyResponse(run_id=run_id, new_version=state.version)
        proposal = await runner.deps.proposals.get(_proposal_key(idea_id, number, proposal_id))
        if proposal is None:
            raise _not_found("unknown proposal: list the proposals again")
        # the current profile: a change to it changes what is recommended (U1)
        profile = await _from("the profile store", runner.deps.store.profile(user_id))
        if profile.user_id != user_id:
            raise _unavailable("the profile store")
        new = await _from(
            "the idea store",
            runner.deps.store.add_version(
                idea_id,
                IdeaVersionCreate(
                    text=append(
                        version.text,
                        CHANGES,
                        [proposal.concrete_change],
                        validate.MAX_IDEA_CHARS,
                    ),
                    origin="whatif",
                    parent_version_id=version.id,
                ),
                key=f"{run_id}:version",
            ),
        )
        await runner.start_validate(idea, new, profile, run_id=run_id)
        return WhatIfApplyResponse(run_id=run_id, new_version=new.version)
