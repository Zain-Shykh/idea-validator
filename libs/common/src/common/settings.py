"""Base settings. Each service subclasses with its own env prefix (conventions §1).

    class VerdictSettings(ServiceSettings):
        model_config = SettingsConfigDict(env_prefix="VERDICT_")
        config_path: str            # VERDICT_CONFIG_PATH

The fields below are shared by all services, so they read unprefixed variables.
URLs have no defaults: they come from the environment (GEN-9).
"""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmBackend = Literal["gateway", "direct", "fake"]


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    agentops_url: str | None = Field(default=None, validation_alias="AGENTOPS_URL")


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    backend: LlmBackend = Field(default="gateway", validation_alias="LLM_BACKEND")
    tools_url: str | None = Field(default=None, validation_alias="TOOLS_URL")
    cassette_path: str | None = Field(default=None, validation_alias="LLM_CASSETTE_PATH")
    ollama_base_url: str | None = Field(default=None, validation_alias="OLLAMA_BASE_URL")
    # provider model ids, used by the 'direct' backend only (in k8s M5 resolves aliases)
    model_agent_default: str = Field(
        default="gemini/gemini-flash-latest", validation_alias="LLM_MODEL_AGENT_DEFAULT"
    )
    model_agent_cheap: str = Field(
        default="gemini/gemini-flash-lite-latest", validation_alias="LLM_MODEL_AGENT_CHEAP"
    )
    model_scorer_hosted: str = Field(
        default="gemini/gemini-flash-latest", validation_alias="LLM_MODEL_SCORER_HOSTED"
    )
    model_local_dev: str = Field(default="ollama/llama3.2", validation_alias="LLM_MODEL_LOCAL_DEV")

    def provider_model(self, alias: str) -> str:
        models = {
            "agent-default": self.model_agent_default,
            "agent-cheap": self.model_agent_cheap,
            "scorer-hosted": self.model_scorer_hosted,
            "local-dev": self.model_local_dev,
        }
        if alias not in models:
            raise ValueError(f"unknown model alias {alias!r}; known: {sorted(models)}")
        return models[alias]
