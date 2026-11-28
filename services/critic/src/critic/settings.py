from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from common.contracts.agents import InputRailRequest
from common.settings import ServiceSettings

_RAIL_LIMIT = InputRailRequest.model_json_schema()["properties"]["text"]["maxLength"]


class CriticSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="CRITIC_", extra="ignore", populate_by_name=True)

    kb_url: str | None = Field(default=None, validation_alias="KB_URL")

    # seconds: one model judgement, then all judgements of one request (AVOID-1)
    judge_timeout_s: float = Field(default=30.0, gt=0, le=60)
    deadline_s: float = Field(default=120.0, gt=0, le=600)
    kb_timeout_s: float = Field(default=10.0, gt=0, le=30)
    max_input_chars: int = Field(default=5000, ge=100, le=_RAIL_LIMIT)

    # relative to the repo root for local runs; the image sets the ConfigMap paths
    prompts_dir: str = "prompts"
    budgets_path: str = "config/budgets.yaml"

    @model_validator(mode="after")
    def _judge_fits_the_deadline(self) -> Self:
        if self.judge_timeout_s > self.deadline_s:
            raise ValueError("the judge timeout must not exceed the request deadline")
        return self
