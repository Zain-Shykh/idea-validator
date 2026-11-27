"""M9 validators: four scorers behind ``POST /score/{scorer}``."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from common.http import SCORER_TIMEOUT, ServiceClient
from common.llm import LLMClient, LLMError
from common.registry import RegistryError, load_prompts, load_rubrics
from common.service import create_app
from common.settings import LlmSettings
from common.trace import TraceClient
from validators.api.routes import router
from validators.services.effort import EffortEstimator
from validators.services.engine import Engine
from validators.services.tools import HttpAnalyst, HttpKnowledgeBase
from validators.settings import ValidatorSettings

log = logging.getLogger("validators")


def build_engine(settings: ValidatorSettings, llm: LlmSettings | None = None) -> Engine | None:
    """None when the service cannot work at all; it then stays unready."""
    if not settings.kb_url or not settings.scorer_url:
        log.error("KB_URL and SCORER_URL are required")
        return None
    tracer = TraceClient(settings.agentops_url)
    client: LLMClient | None = None
    try:
        client = LLMClient(llm, tracer=tracer)
    except (ValueError, LLMError):
        log.error("no model for effort estimates; band midpoints are used", exc_info=True)
    try:
        _, rubrics = load_rubrics(Path(settings.rubrics_dir))
        prompts = load_prompts(Path(settings.prompts_dir))
        if "effort" not in prompts:
            raise RegistryError("the effort prompt is missing")
        return Engine(
            settings,
            kb=HttpKnowledgeBase(settings.kb_url, settings.kb_timeout_s),
            analyst=(
                HttpAnalyst(settings.analyst_url, settings.kb_timeout_s)
                if settings.analyst_url
                else None
            ),
            scorer=ServiceClient(settings.scorer_url, timeout=SCORER_TIMEOUT),
            effort=EffortEstimator(prompts["effort"], client, settings.effort_timeout_s),
            rubrics=rubrics,
            tracer=tracer,
        )
    except RegistryError:
        log.error("invalid rubric or prompt files", exc_info=True)
        return None


async def _close(engine: Engine) -> None:
    await engine.tracer.aclose()
    await engine.scorer.aclose()
    if engine.effort.llm is not None:
        await engine.effort.llm.aclose()
    for tool in (engine.kb, engine.analyst):
        if isinstance(tool, HttpKnowledgeBase | HttpAnalyst):
            await tool.client.aclose()


def build_app(
    settings: ValidatorSettings | None = None, *, engine: Engine | None = None
) -> FastAPI:
    settings = settings or ValidatorSettings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        built: Engine | None = app.state.engine
        if built is None:
            yield
            return
        flush = asyncio.create_task(built.tracer.run_periodic_flush())
        try:
            yield
        finally:
            flush.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flush
            await _close(built)

    # other services being down does not make this pod unready: requests say so themselves
    app = create_app(
        "validators",
        ready_check=lambda: app.state.engine is not None,
        settings=settings,
        lifespan=lifespan,
    )
    app.state.engine = engine or build_engine(settings)
    app.include_router(router)
    return app


app = build_app()
