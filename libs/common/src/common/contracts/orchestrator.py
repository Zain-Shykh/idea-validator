"""M3 Orchestrator API (architecture §7.1, §7.2). Owner: Zain."""

from typing import Literal, Self

from pydantic import Field, model_validator

from common.contracts.base import ContractModel, NonEmptyStr
from common.contracts.core import StudentProfile, Verdict, WhatWouldMakeGo, Workflow


class StartRunRequest(ContractModel):
    workflow: Workflow
    user_id: NonEmptyStr
    idea_version_id: NonEmptyStr | None = None
    niche_wish: str | None = Field(default=None, max_length=500)
    max_ideas: int | None = Field(default=None, ge=1, le=10)
    profile: StudentProfile

    @model_validator(mode="after")
    def _validate_needs_idea(self) -> Self:
        if self.workflow is Workflow.VALIDATE and not self.idea_version_id:
            raise ValueError("a validate run needs idea_version_id")
        return self


class StartRunResponse(ContractModel):
    run_id: NonEmptyStr


class Answer(ContractModel):
    question_id: NonEmptyStr
    answer: NonEmptyStr = Field(max_length=2000)


class AnswerRequest(ContractModel):
    answers: list[Answer] = Field(min_length=1)


class ApproveRequest(ContractModel):
    approval_id: NonEmptyStr
    decision: Literal["approve", "decline"]


class ChallengeRequest(ContractModel):
    version: int = Field(ge=1)
    claim_id: NonEmptyStr


class ChallengeResponse(ContractModel):
    outcome: Literal["defended", "removed"]
    rationale: NonEmptyStr
    verdict: Verdict


class WhatIfRunRequest(ContractModel):
    """Without ``apply``: list proposals. With ``apply``: create the next idea version."""

    version: int = Field(ge=1)
    apply: str | None = None


class WhatIfProposal(ContractModel):
    proposal_id: NonEmptyStr
    concrete_change: NonEmptyStr
    candidate: WhatWouldMakeGo


class WhatIfProposalsResponse(ContractModel):
    proposals: list[WhatIfProposal]


class WhatIfApplyResponse(ContractModel):
    run_id: NonEmptyStr
    new_version: int = Field(ge=2)
