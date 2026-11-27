"""M1 public API bodies (architecture §7.1). Owner: Ahmad (first draft from Z0).

Answer/approve/challenge/what-if bodies are those of ``orchestrator``.
"""

from typing import Literal

from pydantic import Field

from common.contracts.base import ContractModel, NonEmptyStr
from common.contracts.core import IdeaInput

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRequest(ContractModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    password: str = Field(min_length=8, max_length=128, repr=False)


class RegisterResponse(ContractModel):
    user_id: NonEmptyStr


class LoginRequest(ContractModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128, repr=False)


class TokenResponse(ContractModel):
    access_token: NonEmptyStr
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(ge=1)


class DiscoverRequest(ContractModel):
    niche_wish: str | None = Field(default=None, max_length=500)
    max_ideas: int | None = Field(default=None, ge=1, le=10)


class ValidateRequest(ContractModel):
    idea: IdeaInput
    idea_id: str | None = None


class RunAccepted(ContractModel):
    run_id: NonEmptyStr
    idea_id: str | None = None
    version: int | None = Field(default=None, ge=1)
