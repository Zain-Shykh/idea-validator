from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from common.http import VALIDATOR_TIMEOUT
from common.settings import ServiceSettings


class OrchestratorSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="ORCH_", extra="ignore", populate_by_name=True)

    verdict_url: str | None = Field(default=None, validation_alias="VERDICT_URL")
    profile_url: str | None = Field(default=None, validation_alias="PROFILE_URL")
    validators_url: str | None = Field(default=None, validation_alias="VALIDATORS_URL")
    kb_url: str | None = Field(default=None, validation_alias="KB_URL")
    scouts_url: str | None = Field(default=None, validation_alias="SCOUTS_URL")
    critic_url: str | None = Field(default=None, validation_alias="CRITIC_URL")
    ideation_url: str | None = Field(default=None, validation_alias="IDEATION_URL")

    redis_host: str = Field(default="redis", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_password: SecretStr | None = Field(default=None, validation_alias="REDIS_PASSWORD")

    # relative to the repo root for local runs; the image sets the ConfigMap paths
    rubrics_dir: str = "rubrics"
    prompts_dir: str = "prompts"
    budgets_path: str = "config/budgets.yaml"

    # which of "profile" and "verdict" must answer for this pod to take traffic
    ready_requires: str = "profile,verdict"

    # must equal M10's SCORER_MODEL_ID: part of the verdict cache key
    scorer_model_id: str = Field(default="scorer-base@v1", validation_alias="SCORER_MODEL_ID")
    # one scorer call, all its attempts together (D-010)
    scorer_timeout_s: float = Field(default=VALIDATOR_TIMEOUT.read or 600.0, gt=0, le=600)
    search_k: int = Field(default=20, ge=1, le=20)
    deep_search_pages: int = Field(default=15, ge=1, le=100)
    deep_search_page_cost_usd: float = Field(default=0.0005, ge=0)
    scout_sources: str = "hackernews,github"
    wait_timeout_s: int = Field(default=86_400, ge=60)
