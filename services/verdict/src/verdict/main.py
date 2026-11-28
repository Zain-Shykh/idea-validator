import logging
from pathlib import Path

import yaml
from fastapi import FastAPI

from common.service import create_app
from verdict.api.routes import router
from verdict.config import VerdictConfig, load_config
from verdict.settings import VerdictSettings

log = logging.getLogger("verdict")


def build_app(config_path: str | Path | None = None) -> FastAPI:
    """The rules are read once: a ConfigMap change renames the generated ConfigMap, which rolls
    the pods. Without valid rules the app stays up but is not ready and decides nothing."""
    settings = VerdictSettings()
    config: VerdictConfig | None = None
    app = create_app("verdict", ready_check=lambda: config is not None, settings=settings)
    path = config_path or settings.config_path
    try:
        config = load_config(path)
    except (OSError, ValueError, yaml.YAMLError):  # ValidationError is a ValueError
        log.error("invalid verdict config %s", path, exc_info=True)
    app.state.config = config
    app.include_router(router)
    return app


app = build_app()
