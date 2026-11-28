"""M8 ideation, M9 validators, M10 scorer, M12 critic, rubric and fine-tuning formats.

Owner: Zain.
"""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from common.contracts.base import (
    Amount,
    CardId,
    ContractModel,
    Count,
    EvidenceId,
    NonEmptyStr,
    Score,
    Unit,
)
from common.contracts.core import (
    Blocker,
    Budget,
    Claim,
    Dimension,
    DimensionScore,
    DuplicateCandidate,
    EvidenceItem,
    GeneratedIdea,
    IdeaInput,
    Objection,
    StudentProfile,
)

# --- M8 ideation -------------------------------------------------------------------------------


class IntakeRequest(ContractModel):
    run_id: NonEmptyStr
    idea: IdeaInput
    profile: StudentProfile


class IdeaSlots(ContractModel):
    target_user: str
    platform: str
    core_features: list[str]
    scope: str


class PlausibleAnswer(ContractModel):
    answer: NonEmptyStr
    score_deltas: dict[Dimension, float]


class CandidateQuestion(ContractModel):
    question_id: NonEmptyStr
    text: NonEmptyStr
    affects: list[Dimension] = Field(min_length=1)
    answers: list[PlausibleAnswer] = Field(min_length=2, max_length=2)


class IntakeResponse(ContractModel):
    slots: IdeaSlots
    questions: list[CandidateQuestion] = Field(max_length=3)


class NicheContext(ContractModel):
    niche_id: NonEmptyStr
    name: NonEmptyStr
    trend_label: str | None = None


class GapContext(ContractModel):
    niche_id: NonEmptyStr
    statement: NonEmptyStr
    evidence: list[EvidenceItem] = Field(min_length=1)


class IdeateRequest(ContractModel):
    run_id: NonEmptyStr
    profile: StudentProfile
    niches: list[NicheContext]
    gaps: list[GapContext]
    round: int = Field(ge=1, le=3, description="debate is bounded (M8-2)")
    prior_objections: list[Objection] = Field(default_factory=list)


class IdeateResponse(ContractModel):
    ideas: list[GeneratedIdea] = Field(max_length=6)


class GapAnalyzeRequest(ContractModel):
    run_id: NonEmptyStr
    ideas: list[GeneratedIdea]


class EnrichedIdea(ContractModel):
    idea: GeneratedIdea
    complaints: list[Claim] = Field(default_factory=list)
    missing_features: list[Claim] = Field(default_factory=list)


class GapAnalyzeResponse(ContractModel):
    ideas: list[EnrichedIdea]


class SkepticRequest(ContractModel):
    run_id: NonEmptyStr
    idea_id: str | None = None
    idea: IdeaInput
    evidence: list[EvidenceItem]


class SkepticResponse(ContractModel):
    objections: list[Objection]


class DimensionChange(ContractModel):
    dimension: Dimension
    from_score: Score
    to_score: Score


class ConcretizeRequest(ContractModel):
    run_id: NonEmptyStr
    idea: IdeaInput
    changes: list[DimensionChange]


class ConcreteChange(ContractModel):
    dimension: Dimension
    change: NonEmptyStr


class ConcretizeResponse(ContractModel):
    changes: list[ConcreteChange]


# --- M9 validators -----------------------------------------------------------------------------


class ScorerRequest(ContractModel):
    run_id: NonEmptyStr
    idea: IdeaInput
    profile: StudentProfile
    evidence: list[EvidenceItem]


class PartialGateInputs(ContractModel):
    """Each scorer fills only its own fields; M3 merges them into ``GateInputs``."""

    n_sources: Count | None = None
    n_platforms: Count | None = None
    est_effort_hours: Amount | None = None
    available_hours: Amount | None = None
    blockers: list[Blocker] = Field(default_factory=list)
    duplicate: DuplicateCandidate | None = None


class ScorerResponse(ContractModel):
    scores: list[DimensionScore] = Field(min_length=1, max_length=2)
    gate_inputs: PartialGateInputs = Field(default_factory=PartialGateInputs)


# --- M10 scoring model, rubrics, fine-tuning ---------------------------------------------------


class ScoreRequest(ContractModel):
    """API-I3: rubric, idea, evidence in; ``DimensionScore`` out."""

    rubric_id: str = Field(pattern=r"^[a-z_]+@v\d+$")
    idea: IdeaInput
    evidence: list[EvidenceItem]
    features: dict[str, Any] = Field(default_factory=dict)


class ScorerOutput(ContractModel):
    """The JSON a scoring model must produce (also the DATA-7 assistant message)."""

    score: Score
    confidence: Unit
    evidence_ids: list[EvidenceId] = Field(min_length=1)
    reason: NonEmptyStr


class RubricExample(ContractModel):
    input: NonEmptyStr
    expected: dict[str, Any]


class Rubric(ContractModel):
    """DATA-8: one versioned YAML file per dimension."""

    dimension: Dimension
    version: int = Field(ge=1)
    scale: NonEmptyStr
    anchors: dict[int, NonEmptyStr]
    features: list[str]
    output_schema: str
    examples: list[RubricExample] = Field(min_length=1)

    @model_validator(mode="after")
    def _anchor_per_level(self) -> Self:
        if set(self.anchors) != set(range(6)):
            raise ValueError("a rubric needs an anchor for every score 0-5")
        return self


class FineTuneMessage(ContractModel):
    role: Literal["system", "user", "assistant"]
    content: NonEmptyStr


class FineTuneExample(ContractModel):
    """DATA-7: one JSON object per line."""

    messages: list[FineTuneMessage]

    @model_validator(mode="after")
    def _chat_shape(self) -> Self:
        if [m.role for m in self.messages] != ["system", "user", "assistant"]:
            raise ValueError("messages must be system, user, assistant")
        ScorerOutput.model_validate_json(self.messages[2].content)
        return self


# --- M12 critic and guardrails -----------------------------------------------------------------


class CheckRequest(ContractModel):
    claims: list[Claim] = Field(min_length=1)
    strict: bool = False


class ClaimResult(ContractModel):
    claim_id: NonEmptyStr
    supported: bool
    verdict: Literal["supported", "unsupported", "unverifiable"]
    reason: NonEmptyStr

    @model_validator(mode="after")
    def _flag_matches_verdict(self) -> Self:
        if self.supported != (self.verdict == "supported"):
            raise ValueError("supported must be true exactly when verdict is 'supported'")
        return self


class CheckResponse(ContractModel):
    results: list[ClaimResult]


class InputRailRequest(ContractModel):
    text: str = Field(max_length=20000)


class InputRailResponse(ContractModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    redacted_text: str


class ScanRequest(ContractModel):
    evidence: list[EvidenceItem]


class FlaggedEvidence(ContractModel):
    evidence_id: EvidenceId
    patterns: list[str]
    card_id: CardId | None = None


class ScanResponse(ContractModel):
    flagged: list[FlaggedEvidence]


class CostEstimate(ContractModel):
    cost_usd: Amount = 0
    pages: Count = 0
    tokens: Count = 0


class ApprovalPolicyRequest(ContractModel):
    run_id: NonEmptyStr
    action: NonEmptyStr
    estimate: CostEstimate
    remaining: Budget


class ApprovalPolicyResponse(ContractModel):
    requires_approval: bool
    reason: str
