from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from common.contracts.base import NonEmptyStr
from common.http import SCORER_TIMEOUT
from common.settings import ServiceSettings

ScorerBackend = Literal["llamacpp", "ollama", "hosted"]


class ScorerSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="SCORER_", extra="ignore", populate_by_name=True)

    # llamacpp = sidecar (k8s), ollama = host Ollama (dev), hosted = comparison runs only
    backend: ScorerBackend = "llamacpp"
    # address of the llama.cpp server; SCORER_URL is the address *of* this service
    model_url: str | None = None
    ollama_base_url: str | None = Field(default=None, validation_alias="OLLAMA_BASE_URL")
    # model name in the request; llama.cpp serves one model and ignores it
    local_model: str = "qwen2.5:1.5b"
    # recorded as DimensionScore.model for local scores
    model_id: NonEmptyStr = "scorer-base@v1"
    # one deadline for both local attempts, one for the fallback; the sum stays under the
    # caller's 90 s (§21.4)
    local_timeout_s: float = Field(default=60.0, gt=0)
    hosted_timeout_s: float = Field(default=25.0, gt=0)
    probe_interval_s: float = Field(default=5.0, gt=0)

    # relative to the repo root for local runs; the image sets the ConfigMap paths
    rubrics_dir: str = "rubrics"
    prompts_dir: str = "prompts"

    @model_validator(mode="after")
    def _within_the_callers_timeout(self) -> Self:
        limit = SCORER_TIMEOUT.read or 0
        if not self.local_timeout_s + self.hosted_timeout_s < limit:
            raise ValueError(f"the two scorer deadlines must add up to less than {limit:.0f} s")
        return self

    def local_url(self) -> str | None:
        return self.ollama_base_url if self.backend == "ollama" else self.model_url
