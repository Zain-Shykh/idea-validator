"""Shared types: DATA-1..6 and the additions of architecture §7.3 and §21.3.

Owner: Zain. Field lists from the Guide are minimums: add fields, never remove them.
"""

import math
from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from common.contracts.base import (
    Amount,
    CardId,
    ContractModel,
    Count,
    EvidenceId,
    NonEmptyStr,
    Score,
    Score100,
    Unit,
)

# --- enums -------------------------------------------------------------------------------------


class Dimension(StrEnum):
    """The six dimensions. Declaration order is the fixed iteration order of the engine."""

    DEMAND_PAIN = "demand_pain"
    COMPETITION = "competition"
    DIFFERENTIATION = "differentiation"
    FEASIBILITY = "feasibility"
    RISK = "risk"
    LEARNING = "learning"


class VerdictLabel(StrEnum):
    GO = "GO"
    PIVOT = "PIVOT"
    NO_GO = "NO-GO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Stability(StrEnum):
    STABLE = "STABLE"
    BORDERLINE = "BORDERLINE"


class Goal(StrEnum):
    STARTUP = "startup"
    PORTFOLIO = "portfolio"
    COURSE_PROJECT = "course_project"


class Compute(StrEnum):
    LAPTOP = "laptop"
    FREE_GPU = "free_gpu"
    PAID = "paid"


class Workflow(StrEnum):
    DISCOVER = "discover"
    VALIDATE = "validate"


class RunState(StrEnum):
    """Run lifecycle (architecture §6.1)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_INPUT = "AWAITING_INPUT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BUDGET_STOPPED = "BUDGET_STOPPED"
    CANCELLED = "CANCELLED"


class GateResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_CHECKED = "not_checked"


class TraceKind(StrEnum):
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    DECISION = "decision"


class RunEventType(StrEnum):
    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    CACHE_HIT = "cache_hit"
    QUESTION = "question"
    APPROVAL_REQUIRED = "approval_required"
    IDEA_DROPPED = "idea_dropped"
    VERDICT = "verdict"
    RUN_FINISHED = "run_finished"
    ERROR = "error"


class BlockerType(StrEnum):
    LEGAL = "legal"
    SAFETY = "safety"


# --- knowledge and profile ---------------------------------------------------------------------


class IdeaCard(ContractModel):
    """DATA-1."""

    id: CardId
    name: NonEmptyStr
    summary: NonEmptyStr
    problem: str
    customer: str
    niche_tags: list[str]
    stage: str = "unknown"
    funding_usd: int | None = Field(default=None, ge=0)
    source: NonEmptyStr
    url: NonEmptyStr
    published_at: date | datetime | None = None
    fetched_at: date | datetime
    text: NonEmptyStr


class StudentProfile(ContractModel):
    """DATA-2. ``version`` increases on every profile change and is part of cache keys."""

    user_id: NonEmptyStr
    skills: list[str]
    hours_per_week: int = Field(ge=0, le=168)
    weeks_available: int = Field(ge=1)
    team_size: int = Field(ge=1)
    compute: Compute
    goal: Goal
    interests: list[str]
    version: int = Field(default=1, ge=1)


class IdeaInput(ContractModel):
    """The text of an idea, as given to agents."""

    title: NonEmptyStr = Field(max_length=200)
    text: NonEmptyStr = Field(max_length=5000)


class EvidenceItem(ContractModel):
    """DATA-3. ``evidence_id`` is ``<card_id>#c<n>``."""

    evidence_id: EvidenceId
    card_id: CardId
    quote: NonEmptyStr
    source: NonEmptyStr
    url: NonEmptyStr
    retrieved_at: date | datetime

    @model_validator(mode="after")
    def _id_matches_card(self) -> Self:
        if self.evidence_id.split("#", 1)[0] != self.card_id:
            raise ValueError("evidence_id must start with card_id")
        return self


# --- scores and verdict ------------------------------------------------------------------------


class DimensionScore(ContractModel):
    """DATA-4. ``score`` is null when the scorer abstains (M9-4)."""

    dimension: Dimension
    score: Score | None
    confidence: Unit
    evidence_ids: list[EvidenceId]
    features: dict[str, Any] = Field(default_factory=dict)
    reason: NonEmptyStr
    rubric_version: str = Field(pattern=r"^[a-z_]+@v\d+$")
    model: NonEmptyStr

    @model_validator(mode="after")
    def _evidence_or_abstain(self) -> Self:
        if self.score is not None and not self.evidence_ids:
            raise ValueError("a score must cite evidence ids, otherwise abstain (score null)")
        if self.score is None and self.confidence != 0:
            raise ValueError("an abstained dimension has confidence 0")
        if self.rubric_version.split("@")[0] != self.dimension.value:
            raise ValueError("rubric_version must belong to the scored dimension")
        return self


def require_all_dimensions(scores: Iterable[DimensionScore]) -> None:
    """Raise unless each of the six dimensions appears exactly once."""
    if sorted(s.dimension for s in scores) != sorted(Dimension):
        raise ValueError("scores must contain each of the six dimensions exactly once")


class Gates(ContractModel):
    evidence: GateResult
    feasibility: GateResult
    risk: GateResult
    duplicate: GateResult


class Weights(ContractModel):
    demand_pain: Amount
    competition: Amount
    differentiation: Amount
    feasibility: Amount
    risk: Amount
    learning: Amount

    @model_validator(mode="after")
    def _sum_to_100(self) -> Self:
        total = sum(getattr(self, d.value) for d in Dimension)
        # 0.05 tolerates sensitivity weights rounded to two decimals
        if not math.isclose(total, 100, abs_tol=0.05):
            raise ValueError(f"weights must sum to 100, got {total}")
        return self


class Thresholds(ContractModel):
    T_go: Score100
    T_no: Score100
    floor: Score

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.T_no > self.T_go:
            raise ValueError("T_no must not exceed T_go")
        return self


class WhatWouldMakeGo(ContractModel):
    """One change that reaches GO. ``from``/``to`` describe the score step when known."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    change: NonEmptyStr
    new_score: Score100
    new_verdict: VerdictLabel
    dimension: Dimension | None = None
    from_score: Score | None = Field(default=None, alias="from")
    to_score: Score | None = Field(default=None, alias="to")
    stable: bool | None = None


class Sensitivity(ContractModel):
    runs: Count
    flipped: Count

    @model_validator(mode="after")
    def _flipped_within_runs(self) -> Self:
        if self.flipped > self.runs:
            raise ValueError("flipped cannot exceed runs")
        return self


class Versions(ContractModel):
    """Versions recorded on every verdict (AO-3, architecture §8.5)."""

    engine: NonEmptyStr
    rubrics: NonEmptyStr
    profile_weights: NonEmptyStr
    prompts: dict[str, str] = Field(default_factory=dict)
    kb_version: str | None = None


class Verdict(ContractModel):
    """DATA-5 plus ``missing``, ``gate_details``, ``flips``, ``from_cache``. Only M11 builds it."""

    idea_id: NonEmptyStr
    version: int = Field(ge=1)
    run_id: NonEmptyStr
    verdict: VerdictLabel
    stability: Stability
    score: Score100 | None
    confidence: Unit
    gates: Gates
    weights: Weights
    thresholds: Thresholds
    scores: list[DimensionScore]
    weakest_dimension: Dimension | None
    what_would_make_go: list[WhatWouldMakeGo]
    sensitivity: Sensitivity
    versions: Versions
    missing: list[str] = Field(default_factory=list)
    gate_details: dict[str, str] = Field(default_factory=dict)
    flips: list[str] = Field(default_factory=list)
    from_cache: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_all_dimensions(self.scores)
        if self.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE and not self.missing:
            raise ValueError("INSUFFICIENT_EVIDENCE must say what is missing (U10)")
        return self


# --- gates, challenge, ideation ----------------------------------------------------------------


class Blocker(ContractModel):
    type: BlockerType
    description: NonEmptyStr
    evidence_ids: list[EvidenceId] = Field(min_length=1)


class DuplicateCandidate(ContractModel):
    """An existing product, always a knowledge-base card (AVOID-4)."""

    card_id: CardId
    product_name: NonEmptyStr
    same_core_features: bool
    same_user: bool
    similarity: Unit


class GateInputs(ContractModel):
    n_sources: Count
    n_platforms: Count
    est_effort_hours: Amount | None
    available_hours: Amount
    blockers: list[Blocker] = Field(default_factory=list)
    duplicate: DuplicateCandidate | None = None


class Objection(ContractModel):
    """A Skeptic objection. May be unsupported until the critic checks it."""

    objection_id: NonEmptyStr
    idea_id: str | None = None
    dimension: Dimension
    text: NonEmptyStr
    evidence_ids: list[EvidenceId]
    fatal: bool = False


class Claim(ContractModel):
    claim_id: NonEmptyStr
    dimension: Dimension | None = None
    text: NonEmptyStr
    evidence_ids: list[EvidenceId]


class VerdictRequest(ContractModel):
    """Input of M11. ``accepted_objections`` are those the critic found evidence-backed."""

    idea_id: NonEmptyStr
    version: int = Field(ge=1)
    run_id: NonEmptyStr
    goal: Goal
    profile: StudentProfile
    scores: list[DimensionScore]
    gate_inputs: GateInputs
    accepted_objections: list[Objection] = Field(default_factory=list)
    rubric_set: NonEmptyStr
    kb_version: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_all_dimensions(self.scores)
        if self.goal != self.profile.goal:
            raise ValueError("goal must equal profile.goal")
        if any(not o.evidence_ids for o in self.accepted_objections):
            raise ValueError("an accepted objection must cite evidence ids")
        return self


class GeneratedIdea(ContractModel):
    title: NonEmptyStr
    text: NonEmptyStr
    niche_id: NonEmptyStr
    target_user: NonEmptyStr
    gap_evidence_ids: list[EvidenceId] = Field(min_length=1)
    rationale: NonEmptyStr


# --- runs --------------------------------------------------------------------------------------


class Budget(ContractModel):
    max_tokens: Count
    max_cost_usd: Amount
    max_pages: Count
    max_steps: Count


class Spend(ContractModel):
    tokens: Count = 0
    cost_usd: Amount = 0
    pages: Count = 0
    steps: Count = 0


class RunEvent(ContractModel):
    run_id: NonEmptyStr
    seq: Count
    ts: datetime
    type: RunEventType
    agent: str | None = None
    step: str | None = None
    message: str
    data: dict[str, Any] | None = None
    spend: Spend


class ClarifyQuestion(ContractModel):
    question_id: NonEmptyStr
    text: NonEmptyStr


class ApprovalRequest(ContractModel):
    """What the student is asked to approve, with its stated cost (U4)."""

    approval_id: NonEmptyStr
    action: NonEmptyStr
    estimated_cost_usd: Amount
    estimated_pages: Count


class PendingInput(ContractModel):
    kind: Literal["question", "approval"]
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    approval: ApprovalRequest | None = None

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Self:
        if self.kind == "question" and not self.questions:
            raise ValueError("kind 'question' needs questions")
        if self.kind == "approval" and self.approval is None:
            raise ValueError("kind 'approval' needs approval")
        return self


class RunResult(ContractModel):
    """Verdict cards, ranked for Discover (one entry for Validate)."""

    verdicts: list[Verdict]
    reasons: dict[str, str] = Field(default_factory=dict, description="idea_id -> one-line reason")


class RunStatus(ContractModel):
    run_id: NonEmptyStr
    workflow: Workflow
    status: RunState
    created_at: datetime
    updated_at: datetime
    budget: Budget
    spend: Spend
    pending: PendingInput | None = None
    result: RunResult | None = None


# --- tracing and errors ------------------------------------------------------------------------


class TraceRecord(ContractModel):
    """DATA-6. One per agent step, tool call, model call and orchestrator decision."""

    run_id: NonEmptyStr
    step_id: Count
    parent_step: Count | None = None
    agent: NonEmptyStr
    kind: TraceKind
    tool: str | None = None
    input: str | None = None
    output: str | None = None
    tokens_in: Count = 0
    tokens_out: Count = 0
    cost_usd: Amount = 0
    latency_ms: Count = 0
    retries: Count = 0
    model: str | None = None
    cache_hit: bool = False
    error: str | None = None
    ts: datetime
    prompt_version: str | None = None


class ErrorBody(ContractModel):
    code: NonEmptyStr
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorEnvelope(ContractModel):
    error: ErrorBody


class HealthStatus(ContractModel):
    status: str
