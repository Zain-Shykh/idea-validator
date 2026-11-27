import pytest
from pydantic_settings import SettingsConfigDict

from common.settings import LlmSettings, ServiceSettings


class DemoSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="DEMO_")
    config_path: str = "config.yaml"


def test_prefixed_and_shared_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_CONFIG_PATH", "/etc/demo.yaml")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AGENTOPS_URL", "http://agentops:8000")
    settings = DemoSettings()
    assert settings.config_path == "/etc/demo.yaml"
    assert (settings.log_level, settings.agentops_url) == ("DEBUG", "http://agentops:8000")


def test_urls_have_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENTOPS_URL", "TOOLS_URL", "OLLAMA_BASE_URL", "LLM_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    assert ServiceSettings().agentops_url is None
    assert LlmSettings().tools_url is None
    assert LlmSettings().ollama_base_url is None
    assert LlmSettings().backend == "gateway"


def test_unknown_alias_rejected() -> None:
    with pytest.raises(ValueError, match="unknown model alias"):
        LlmSettings().provider_model("gpt")
