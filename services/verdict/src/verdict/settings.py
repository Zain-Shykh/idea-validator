from pydantic_settings import SettingsConfigDict

from common.settings import ServiceSettings


class VerdictSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="VERDICT_", extra="ignore", populate_by_name=True)

    # relative to the repo root for local runs; the image sets the ConfigMap mount path
    config_path: str = "config/verdict.yaml"
