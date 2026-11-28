"""M10 scorer service: rubric-based scoring behind ``POST /score``."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from common.contracts.tools import ChatMessage
from common.llm import LLMClient, LLMError
from common.registry import RegistryError, load_prompts, load_rubrics
from common.service import create_app
from common.settings import LlmSettings
from common.trace import TraceClient
from scorer.api.routes import router
from scorer.services.backend import LocalModel, LocalModelError
from scorer.services.scoring import Scorer
from scorer.settings import ScorerSettings

log = logging.getLogger("scorer")

_PROBE = [ChatMessage(role="user", content="Reply with the word ok.")]


def build_scorer(settings: ScorerSettings, llm: LlmSettings | None = None) -> Scorer | None:
    """None when the service cannot score at all; it then stays unready."""
    local: LocalModel | None = None
    if settings.backend != "hosted":
        url = settings.local_url()
        if not url:
            log.error("no model address: set SCORER_MODEL_URL (llamacpp) or OLLAMA_BASE_URL")
            return None
        try:
            local = LocalModel(url, settings.local_model, timeout_s=settings.local_timeout_s)
        except (httpx.InvalidURL, ValueError):
            log.error("the model address is not a valid URL")
            return None
    tracer = TraceClient(settings.agentops_url)
    hosted: LLMClient | None = None
    try:
        hosted = LLMClient(llm, tracer=tracer)
    except (ValueError, LLMError):
        log.error("hosted fallback is not configured", exc_info=True)
        if local is None:
            return None
    try:
        _, rubrics = load_rubrics(Path(settings.rubrics_dir))
        prompts = load_prompts(Path(settings.prompts_dir))
        if "scorer" not in prompts:
            raise RegistryError("the scorer prompt is missing")
        return Scorer(
            rubrics,
            prompts["scorer"],
            local=local,
            hosted=hosted,
            tracer=tracer,
            model_id=settings.model_id,
            local_timeout_s=settings.local_timeout_s,
            hosted_timeout_s=settings.hosted_timeout_s,
        )
    except RegistryError:
        log.error("invalid rubric or prompt files", exc_info=True)
        return None


async def probe(local: LocalModel, ready: asyncio.Event, interval_s: float) -> None:
    """Ready only once the model has answered (it may still be loading). Runs in the background
    because a first reply can take longer than a readiness check may."""
    while not ready.is_set():
        try:
            await local.complete(_PROBE, max_tokens=4)
            ready.set()
            log.info("model answered the probe prompt")
        except LocalModelError as exc:
            log.info("model not ready: %s", exc)
            await asyncio.sleep(interval_s)


def build_app(settings: ScorerSettings | None = None, *, scorer: Scorer | None = None) -> FastAPI:
    settings = settings or ScorerSettings()
    answered = asyncio.Event()

    def ready() -> bool:
        built = app.state.scorer
        return built is not None and (built.local is None or answered.is_set())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        built: Scorer | None = app.state.scorer
        if built is None:
            yield
            return
        tasks = [asyncio.create_task(built.tracer.run_periodic_flush())]
        if built.local is not None:
            tasks.append(
                asyncio.create_task(probe(built.local, answered, settings.probe_interval_s))
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await built.tracer.aclose()
            if built.local is not None:
                await built.local.aclose()
            if built.hosted is not None:
                await built.hosted.aclose()

    app = create_app("scorer", ready_check=ready, settings=settings, lifespan=lifespan)
    app.state.scorer = scorer or build_scorer(settings)
    app.include_router(router)
    return app


app = build_app()
