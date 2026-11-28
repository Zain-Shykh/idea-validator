"""The state machines of M3 and the checkpoints that make a run resumable (ADR-004).

The run lifecycle is §6.1; the Validate steps are §6.2 plus the verdict-cache shortcut of §6.3;
the Discover steps are §6.4. Anything not in a table is an error, so a bug cannot skip a step or
restart a finished run.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from common.contracts.agents import (
    CandidateQuestion,
    GapContext,
    NicheContext,
    ScorerResponse,
)
from common.contracts.base import ContractModel, Count, NonEmptyStr
from common.contracts.core import (
    Budget,
    EvidenceItem,
    GeneratedIdea,
    IdeaInput,
    Objection,
    PendingInput,
    RunResult,
    RunState,
    Spend,
    StudentProfile,
    Verdict,
    VerdictRequest,
    Workflow,
)


class Step(StrEnum):
    INTAKE = "INTAKE"
    EVIDENCE = "EVIDENCE"
    DEEP_SEARCH = "DEEP_SEARCH"
    FEATURES = "FEATURES"
    SCORING = "SCORING"
    PRELIM_VERDICT = "PRELIM_VERDICT"
    CLARIFY = "CLARIFY"
    CHALLENGE = "CHALLENGE"
    VERDICT = "VERDICT"
    STABILITY = "STABILITY"
    CARD = "CARD"


_R = RunState
RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    _R.QUEUED: frozenset({_R.RUNNING}),
    _R.RUNNING: frozenset(
        {_R.AWAITING_INPUT, _R.AWAITING_APPROVAL, _R.COMPLETED, _R.BUDGET_STOPPED, _R.FAILED}
    ),
    _R.AWAITING_INPUT: frozenset({_R.RUNNING, _R.CANCELLED}),
    _R.AWAITING_APPROVAL: frozenset({_R.RUNNING, _R.CANCELLED}),
    _R.COMPLETED: frozenset(),
    _R.FAILED: frozenset(),
    _R.BUDGET_STOPPED: frozenset(),
    _R.CANCELLED: frozenset(),
}
TERMINAL = frozenset(state for state, after in RUN_TRANSITIONS.items() if not after)
WAITING = frozenset({_R.AWAITING_INPUT, _R.AWAITING_APPROVAL})

_S = Step
STEP_TRANSITIONS: dict[Step, frozenset[Step]] = {
    _S.INTAKE: frozenset({_S.EVIDENCE, _S.CARD}),  # CARD: verdict cache hit
    _S.EVIDENCE: frozenset({_S.DEEP_SEARCH, _S.FEATURES}),
    _S.DEEP_SEARCH: frozenset({_S.EVIDENCE}),
    _S.FEATURES: frozenset({_S.SCORING}),
    _S.SCORING: frozenset({_S.PRELIM_VERDICT}),
    _S.PRELIM_VERDICT: frozenset({_S.CLARIFY, _S.CHALLENGE}),
    _S.CLARIFY: frozenset({_S.SCORING}),
    _S.CHALLENGE: frozenset({_S.VERDICT}),
    _S.VERDICT: frozenset({_S.STABILITY}),
    _S.STABILITY: frozenset({_S.CARD}),
    _S.CARD: frozenset(),
}


class DiscoverStep(StrEnum):
    REQUEST = "REQUEST"
    NICHES = "NICHES"
    IDEATE = "IDEATE"
    GAP_ANALYZE = "GAP_ANALYZE"
    SKEPTIC = "SKEPTIC"
    CRITIC = "CRITIC"
    FILTER = "FILTER"
    VALIDATE_EACH = "VALIDATE_EACH"
    RESULT = "RESULT"


_D = DiscoverStep
DISCOVER_TRANSITIONS: dict[DiscoverStep, frozenset[DiscoverStep]] = {
    _D.REQUEST: frozenset({_D.NICHES}),
    _D.NICHES: frozenset({_D.IDEATE, _D.RESULT}),  # RESULT: no gap evidence, no ideas
    _D.IDEATE: frozenset({_D.GAP_ANALYZE, _D.VALIDATE_EACH}),  # a later round failed
    _D.GAP_ANALYZE: frozenset({_D.SKEPTIC}),
    _D.SKEPTIC: frozenset({_D.CRITIC}),
    _D.CRITIC: frozenset({_D.FILTER}),
    _D.FILTER: frozenset({_D.IDEATE, _D.VALIDATE_EACH}),
    _D.VALIDATE_EACH: frozenset({_D.RESULT}),
    _D.RESULT: frozenset(),
}
MAX_ROUNDS = 3  # M8-2


class InvalidTransitionError(Exception):
    pass


def check_run_transition(current: RunState, new: RunState) -> None:
    if new not in RUN_TRANSITIONS[current]:
        raise InvalidTransitionError(f"a run cannot go from {current} to {new}")


def check_step_transition(current: Step, new: Step) -> None:
    if new not in STEP_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Validate cannot go from {current} to {new}")


def check_discover_transition(current: DiscoverStep, new: DiscoverStep) -> None:
    if new not in DISCOVER_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Discover cannot go from {current} to {new}")


# not_needed -> asked (waiting) -> approved | declined; approved -> started -> done
DeepSearch = Literal["no", "asked", "approved", "declined", "started", "done"]


class BaseCheckpoint(ContractModel):
    """What every run needs to be continued; stored in M2 (``runs.state``). Private to M3."""

    workflow: Workflow
    user_id: NonEmptyStr
    status: RunState = RunState.QUEUED
    created_at: datetime
    updated_at: datetime
    profile: StudentProfile

    budget: Budget
    spend: Spend = Field(default_factory=Spend)
    pending: PendingInput | None = None
    waiting_since: datetime | None = None

    # why a card must not be put in the verdict cache, or why a result is thinner than usual
    degraded: list[str] = Field(default_factory=list)
    error_code: str | None = None

    def degrade(self, reason: str) -> None:
        if reason not in self.degraded:
            self.degraded.append(reason)

    def own_spend(self) -> Spend:
        """What this run spent itself, without any child run."""
        return self.spend

    def set_own_spend(self, spend: Spend) -> None:
        self.spend = spend


class Checkpoint(BaseCheckpoint):
    """A Validate run."""

    step: Step = Step.INTAKE

    idea_id: NonEmptyStr
    idea_version_id: NonEmptyStr
    version: int = Field(ge=1)
    idea: IdeaInput

    # set on the child runs of a Discover run: the parent drives them and they never wait
    parent_run_id: str | None = None
    # a cache hit: the run whose checkpoint holds the engine input of this verdict
    source_run_id: str | None = None
    # set on the run a challenge made by removing a claim: the Critic's reason
    challenge_rationale: str | None = None

    engine_version: str | None = None
    weights_version: str | None = None
    kb_version: str | None = None
    min_sources: Count = 0
    min_platforms: Count = 0

    # what the agents see: the rail's redacted text
    safe_idea: IdeaInput | None = None
    candidate_questions: list[CandidateQuestion] = Field(default_factory=list)
    # Skeptic objections the Critic found supported
    objections: list[Objection] = Field(default_factory=list)
    clarified: bool = False
    deep_search: DeepSearch = "no"
    # what the estimate said, and so what is bought, even if the setting changes meanwhile
    deep_search_pages: Count = 0
    evidence: list[EvidenceItem] = Field(default_factory=list)
    available_hours: Count = 0
    # the idea each scorer was given: unchanged for scorers a clarify answer does not affect
    scorer_ideas: dict[str, IdeaInput] = Field(default_factory=dict)
    scorer_results: dict[str, ScorerResponse] = Field(default_factory=dict)
    # the last VerdictRequest sent to M11: original (pre-penalty) scores and accepted objections
    verdict_request: VerdictRequest | None = None
    verdict: Verdict | None = None
    cached: bool = False


class Candidate(ContractModel):
    """An idea in the debate (§6.4)."""

    key: NonEmptyStr
    idea: GeneratedIdea
    gap_evidence: list[EvidenceItem] = Field(default_factory=list)
    # what the Gap Analyst's claims cite and what M6 found for the idea
    found_evidence: list[EvidenceItem] = Field(default_factory=list)
    # the Skeptic's objections until the Critic has checked them, then only the supported ones
    objections: list[Objection] = Field(default_factory=list)


ChildOutcome = Literal["pending", "done", "budget", "failed"]


class Child(ContractModel):
    """The Validate run of one survivor. Its id is stored before the run is created."""

    run_id: NonEmptyStr
    idea: GeneratedIdea
    outcome: ChildOutcome = "pending"
    spend: Spend = Field(default_factory=Spend)
    verdict: Verdict | None = None


class DiscoverCheckpoint(BaseCheckpoint):
    step: DiscoverStep = DiscoverStep.REQUEST

    niche_wish: str | None = None
    target: int = Field(ge=1, le=10)
    effort_margin: float = Field(default=1.0, gt=0)

    niches: list[NicheContext] = Field(default_factory=list)
    gaps: list[GapContext] = Field(default_factory=list)
    round: int = Field(default=0, ge=0, le=MAX_ROUNDS)
    candidates: list[Candidate] = Field(default_factory=list)
    survivors: list[Candidate] = Field(default_factory=list)
    # supported non-fatal objections of the last round: revision hints for the Ideator
    hints: list[Objection] = Field(default_factory=list)
    # digests of the ideas already debated
    seen: list[str] = Field(default_factory=list)
    children: list[Child] = Field(default_factory=list)
    debate_spend: Spend = Field(default_factory=Spend)
    # why there is nothing to show, when there is nothing to show
    note: str | None = None
    result: RunResult | None = None

    def own_spend(self) -> Spend:
        return self.debate_spend

    def set_own_spend(self, spend: Spend) -> None:
        self.debate_spend = spend
        self.total_spend()

    def total_spend(self) -> None:
        """The run's spend is the debate's plus every child's (AO-2)."""
        parts = [self.debate_spend, *(c.spend for c in self.children)]
        self.spend = Spend(
            **{name: sum(getattr(p, name) for p in parts) for name in Spend.model_fields}
        )
