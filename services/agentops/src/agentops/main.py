import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from agentops.api.routes import router
from agentops.repositories.files import TraceFiles
from agentops.repositories.index import IndexRepository
from agentops.services.ingest import TraceService
from agentops.settings import AgentOpsSettings
from common.contracts.ops import RegistryEntry
from common.registry import RegistryError, registry_entries
from common.service import create_app

log = logging.getLogger("agentops")


def build_app(settings: AgentOpsSettings | None = None) -> FastAPI:
    """Ready needs a migrated database, a writable trace volume and a valid registry. The registry
    is read once: a ConfigMap change renames the generated ConfigMap, which rolls the pod."""
    settings = settings or AgentOpsSettings()
    files = TraceFiles(settings.traces_dir)
    url = settings.db_url()
    index = IndexRepository(url) if url is not None else None
    entries: list[RegistryEntry] | None = None

    def ready() -> bool:
        return entries is not None and index is not None and index.ping() and files.writable()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if index is not None:
            index.dispose()

    app = create_app("agentops", ready_check=ready, settings=settings, lifespan=lifespan)
    try:
        entries = registry_entries(
            Path(settings.rubrics_dir), Path(settings.prompts_dir), Path(settings.config_dir)
        )
    except RegistryError:
        log.error("invalid rubric or prompt registry", exc_info=True)
    if index is None:
        log.error("no database configured (set POSTGRES_HOST or AGENTOPS_DATABASE_URL)")
    app.state.registry = entries
    app.state.traces = TraceService(files, index)
    app.include_router(router)
    return app


app = build_app()
