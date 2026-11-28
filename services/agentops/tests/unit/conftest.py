from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentops.main import build_app
from agentops.migrate import upgrade
from agentops.settings import AgentOpsSettings

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def settings(tmp_path: Path) -> AgentOpsSettings:
    url = f"sqlite:///{tmp_path}/ops.db"
    upgrade(url)  # the real migration; SQLite stands in for Postgres in unit tests
    return AgentOpsSettings(
        database_url=url,
        traces_dir=str(tmp_path / "traces"),
        rubrics_dir=str(ROOT / "rubrics"),
        prompts_dir=str(ROOT / "prompts"),
        config_dir=str(ROOT / "config"),
    )


@pytest.fixture
def client(settings: AgentOpsSettings) -> Iterator[TestClient]:
    with TestClient(build_app(settings)) as test_client:
        yield test_client
