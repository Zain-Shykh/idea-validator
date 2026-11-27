"""The two state machines of M3 and the checkpoint that makes a run resumable (ADR-004).

The run lifecycle is §6.1; the Validate steps are §6.2 plus the verdict-cache shortcut of §6.3.
Anything not in a table is an error, so a bug cannot skip a step or restart a finished run.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from common.contracts.agents import CandidateQuestion, ScorerResponse
from common.contracts.base import ContractModel, Count, NonEmptyStr
from common.contracts.core import (
    Budget,
    EvidenceItem,
    IdeaInput,
    Objection,
    PendingInput,
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


class InvalidTransitionError(Exception):
    pass


def check_run_transition(current: RunState, new: RunState) -> None:
    if new not in RUN_TRANSITIONS[current]:
        raise InvalidTransitionError(f"a run cannot go from {current} to {new}")


def check_step_transition(current: Step, new: Step) -> None:
    if new not in STEP_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Validate cannot go from {current} to {new}")


# not_needed -> asked (waiting) -> approved | declined; approved -> started -> done
DeepSearch = Literal["no", "asked", "approved", "declined", "started", "done"]


class Checkpoint(ContractModel):
    """Everything needed to continue a run; stored in M2 (``runs.state``). Private to M3."""

    workflow: Workflow
    user_id: NonEmptyStr
    status: RunState = RunState.QUEUED
    step: Step = Step.INTAKE
    created_at: datetime
    updated_at: datetime

    idea_id: NonEmptyStr
    idea_version_id: NonEmptyStr
    version: int = Field(ge=1)
    idea: IdeaInput
    profile: StudentProfile

    budget: Budget
    spend: Spend = Field(default_factory=Spend)
    pending: PendingInput | None = None
    waiting_since: datetime | None = None

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
    evidence: list[EvidenceItem] = Field(default_factory=list)
    available_hours: Count = 0
    # the idea each scorer was given: unchanged for scorers a clarify answer does not affect
    scorer_ideas: dict[str, IdeaInput] = Field(default_factory=dict)
    scorer_results: dict[str, ScorerResponse] = Field(default_factory=dict)
    # the last VerdictRequest sent to M11: original (pre-penalty) scores and accepted objections
    verdict_request: VerdictRequest | None = None
    verdict: Verdict | None = None
    cached: bool = False
    # why the card must not be put in the verdict cache
    degraded: list[str] = Field(default_factory=list)
    error_code: str | None = None

    def degrade(self, reason: str) -> None:
        if reason not in self.degraded:
            self.degraded.append(reason)
