from pydantic import Field, SecretStr

from common.settings import ServiceSettings


class OrchestratorSettings(ServiceSettings):
    verdict_url: str | None = Field(default=None, validation_alias="VERDICT_URL")
    redis_host: str = Field(default="redis", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_password: SecretStr | None = Field(default=None, validation_alias="REDIS_PASSWORD")
