from pydantic import Field
from pydantic_settings import SettingsConfigDict

from common.settings import ServiceSettings


class IdeationSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="IDEATION_", extra="ignore", populate_by_name=True)

    # the gap analyst searches M6
    kb_url: str | None = Field(default=None, validation_alias="KB_URL")
    kb_timeout_s: float = Field(default=10.0, gt=0, le=30)
    search_k: int = Field(default=8, ge=1, le=20)
    # one agent call with its repair retry; M3 gives a request 60 s (AVOID-1)
    llm_timeout_s: float = Field(default=50.0, gt=0, le=60)

    # relative to the repo root for local runs; the image sets the ConfigMap path
    prompts_dir: str = "prompts"
