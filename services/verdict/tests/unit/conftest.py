import pytest
from verdict_factories import CONFIG_PATH

from verdict.config import VerdictConfig, load_config


@pytest.fixture(scope="session")
def config() -> VerdictConfig:
    return load_config(CONFIG_PATH)
