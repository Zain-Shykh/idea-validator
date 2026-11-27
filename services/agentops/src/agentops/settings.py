from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import URL, make_url

from common.settings import ServiceSettings


class AgentOpsSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTOPS_", extra="ignore", populate_by_name=True)

    # relative to the repo root for local runs; the image sets the volume and ConfigMap paths
    traces_dir: str = "traces"
    rubrics_dir: str = "rubrics"
    prompts_dir: str = "prompts"
    config_dir: str = "config"

    # full URL (AGENTOPS_DATABASE_URL) wins over the shared POSTGRES_* parts
    database_url: SecretStr | None = None
    postgres_host: str | None = Field(default=None, validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    postgres_user: str = Field(default="ideaval", validation_alias="POSTGRES_USER")
    postgres_password: SecretStr | None = Field(default=None, validation_alias="POSTGRES_PASSWORD")
    ops_db: str = Field(default="ops", validation_alias="OPS_DB")

    def db_url(self) -> URL | None:
        if self.database_url and self.database_url.get_secret_value():
            return make_url(self.database_url.get_secret_value())
        if not self.postgres_host:
            return None
        password = self.postgres_password
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=password.get_secret_value() if password else None,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.ops_db,
        )
