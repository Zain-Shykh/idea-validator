"""M2 internal API (architecture §7.2, §8.1). Owner: Ahmad (first draft from Z0).

Profiles are ``core.StudentProfile`` (it carries ``version``).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from common.contracts.base import Amount, ContractModel, Count, NonEmptyStr
from common.contracts.core import (
    ApprovalRequest,
    Budget,
    Claim,
    DimensionScore,
    RunState,
    Spend,
    Verdict,
    Workflow,
)


class UserCredentials(ContractModel):
    email: NonEmptyStr
    password: str = Field(min_length=8, max_length=128, repr=False)


class User(ContractModel):
    user_id: NonEmptyStr
    email: NonEmptyStr


class IdeaCreate(ContractModel):
    user_id: NonEmptyStr
    title: NonEmptyStr
    text: NonEmptyStr
    source: Literal["own", "generated"]
    niche_id: str | None = None


class IdeaVersionCreate(ContractModel):
    text: NonEmptyStr
    origin: Literal["initial", "whatif", "challenge", "clarify"]
    parent_version_id: str | None = None


class IdeaVersion(IdeaVersionCreate):
    id: NonEmptyStr
    idea_id: NonEmptyStr
    version: int = Field(ge=1)
    created_at: datetime


class Idea(ContractModel):
    id: NonEmptyStr
    user_id: NonEmptyStr
    title: NonEmptyStr
    source: Literal["own", "generated"]
    niche_id: str | None = None
    created_at: datetime
    versions: list[IdeaVersion] = Field(default_factory=list)


class RunCreate(ContractModel):
    run_id: NonEmptyStr
    user_id: NonEmptyStr
    workflow: Workflow
    profile_version: int = Field(ge=1)
    budget: Budget
    parent_run_id: str | None = None


class RunUpdate(ContractModel):
    """``state`` is M3's checkpoint (architecture §6.1)."""

    status: RunState | None = None
    state: dict[str, Any] | None = None
    spend: Spend | None = None
    result: dict[str, Any] | None = None


class RunRecord(RunCreate):
    status: RunState
    state: dict[str, Any] = Field(default_factory=dict)
    spend: Spend = Field(default_factory=Spend)
    result: dict[str, Any] | None = None
    created_at: datetime


class ApprovalCreate(ContractModel):
    run_id: NonEmptyStr
    request: ApprovalRequest


class ApprovalUpdate(ContractModel):
    decision: Literal["approved", "declined"]


class Approval(ApprovalCreate):
    decision: Literal["pending", "approved", "declined"]
    decided_at: datetime | None = None


class VerdictStore(ContractModel):
    idea_version_id: NonEmptyStr
    verdict: Verdict
    claims: list[Claim] = Field(default_factory=list)


class VerdictStored(ContractModel):
    verdict_id: NonEmptyStr


class CachedVerdict(ContractModel):
    hit: bool
    verdict: Verdict | None = None


class ComparedIdea(ContractModel):
    idea_id: NonEmptyStr
    title: NonEmptyStr
    verdict: str
    overall_score: Amount | None
    scores: list[DimensionScore]
    version: Count


class CompareResponse(ContractModel):
    a: ComparedIdea
    b: ComparedIdea
