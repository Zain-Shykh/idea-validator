from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from common.http import SCORER_TIMEOUT, VALIDATOR_TIMEOUT
from common.settings import ServiceSettings


class ValidatorSettings(ServiceSettings):
    model_config = SettingsConfigDict(
        env_prefix="VALIDATORS_", extra="ignore", populate_by_name=True
    )

    kb_url: str | None = Field(default=None, validation_alias="KB_URL")
    analyst_url: str | None = Field(default=None, validation_alias="ANALYST_URL")
    scorer_url: str | None = Field(default=None, validation_alias="SCORER_URL")

    # M10 calls in flight. 1 suits one CPU model (about 35 s a score); raise it with more slots
    scorer_concurrency: int = Field(default=1, ge=1, le=16)
    scorer_timeout_s: float = Field(default=90.0, gt=0)
    # one request, including the wait for a slot; six calls of 90 s fit
    deadline_s: float = Field(default=570.0, gt=0)
    kb_timeout_s: float = Field(default=10.0, gt=0, le=30)
    effort_timeout_s: float = Field(default=20.0, gt=0, le=60)

    # bounds on what is sent to the model (CPU latency, spec Z4 C4)
    search_k: int = Field(default=20, ge=1, le=50)
    max_evidence: int = Field(default=4, ge=1, le=12)
    max_quote_chars: int = Field(default=300, ge=50, le=500)
    max_idea_chars: int = Field(default=1200, ge=100, le=3000)

    # calibration knobs for M6's similarity scale; re-tune on the real knowledge base
    min_similarity: float = Field(default=0.2, ge=0, le=1)
    competitor_similarity: float = Field(default=0.75, ge=0, le=1)
    duplicate_similarity: float = Field(default=0.9, ge=0, le=1)

    # relative to the repo root for local runs; the image sets the ConfigMap paths
    rubrics_dir: str = "rubrics"
    prompts_dir: str = "prompts"

    @model_validator(mode="after")
    def _timeouts_fit_the_callers(self) -> Self:
        if self.scorer_timeout_s > (SCORER_TIMEOUT.read or 0):
            raise ValueError("the scorer timeout must not exceed the 90 s of §21.4")
        if not self.deadline_s < (VALIDATOR_TIMEOUT.read or 0):
            raise ValueError("the deadline must be below the caller's VALIDATOR_TIMEOUT")
        return self
