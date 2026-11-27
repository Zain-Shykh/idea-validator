"""M8 ideation: intake, ideator, gap analyst, skeptic and concretizer."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from common.llm import LLMClient, LLMError
from common.registry import RegistryError, load_prompts
from common.service import create_app
from common.settings import LlmSettings
from common.trace import TraceClient
from ideation.api.routes import router
from ideation.services.agents import Agents
from ideation.services.kb import HttpKnowledgeBase
from ideation.settings import IdeationSettings

log = logging.getLogger("ideation")


def build_agents(
    settings: IdeationSettings, tracer: TraceClient, llm: LlmSettings | None = None
) -> Agents | None:
    """None when the service cannot work at all; it then stays unready."""
    if not settings.kb_url:
        log.error("KB_URL is required")
        return None
    try:
        return Agents(
            load_prompts(Path(settings.prompts_dir)),
            LLMClient(llm, tracer=tracer),
            HttpKnowledgeBase(settings.kb_url, settings.kb_timeout_s),
            tracer,
            search_k=settings.search_k,
            llm_timeout_s=settings.llm_timeout_s,
        )
    except (ValueError, LLMError, RegistryError):
        log.error("no model backend or invalid prompts", exc_info=True)
        return None


def build_app(
    settings: IdeationSettings | None = None, *, agents: Agents | None = None
) -> FastAPI:
    settings = settings or IdeationSettings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        flush = asyncio.create_task(app.state.tracer.run_periodic_flush())
        try:
            yield
        finally:
            flush.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flush
            await app.state.tracer.aclose()
            built: Agents | None = app.state.agents
            if built is not None:
                await built.llm.aclose()
                if isinstance(built.kb, HttpKnowledgeBase):
                    await built.kb.client.aclose()

    # other services being down does not make this pod unready: requests say so themselves
    app = create_app(
        "ideation",
        ready_check=lambda: app.state.agents is not None,
        settings=settings,
        lifespan=lifespan,
    )
    app.state.tracer = agents.tracer if agents else TraceClient(settings.agentops_url)
    app.state.agents = agents or build_agents(settings, app.state.tracer)
    app.include_router(router)
    return app


app = build_app()
