"""M12 critic and guardrails: claim check, input rail, evidence scan, approval policy."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from common.llm import LLMClient, LLMError
from common.registry import BudgetConfig, RegistryError, load_budgets, load_prompt
from common.service import create_app
from common.settings import LlmSettings
from common.trace import TraceClient
from critic.api.routes import router
from critic.services.check import Checker
from critic.services.judge import Judge
from critic.services.kb import HttpKnowledgeBase
from critic.settings import CriticSettings

log = logging.getLogger("critic")


def _prompt_path(directory: Path) -> Path:
    """``critic_check.md`` as mounted from the ConfigMap, else the versioned file in the repo."""
    mounted = directory / "critic_check.md"
    if mounted.exists():
        return mounted
    versions = sorted(directory.glob("critic_check@v*.md"))
    # the registry check keeps one version in the repo
    return versions[0] if len(versions) == 1 else mounted


def build_checker(
    settings: CriticSettings, tracer: TraceClient, llm: LlmSettings | None = None
) -> Checker | None:
    """None when claims cannot be checked at all; the service then stays unready."""
    if not settings.kb_url:
        log.error("KB_URL is required")
        return None
    client: LLMClient | None = None
    try:
        client = LLMClient(llm, tracer=tracer)
    except (ValueError, LLMError):
        log.error("no judge model; claims that need it are unverifiable", exc_info=True)
    try:
        prompt = load_prompt(_prompt_path(Path(settings.prompts_dir)))
        judge = Judge(prompt, client, settings.judge_timeout_s)
    except RegistryError:
        log.error("invalid critic_check prompt", exc_info=True)
        return None
    kb = HttpKnowledgeBase(settings.kb_url, settings.kb_timeout_s)
    return Checker(kb, judge, tracer, settings.deadline_s)


def _load_budgets(settings: CriticSettings) -> BudgetConfig | None:
    try:
        return load_budgets(Path(settings.budgets_path))
    except RegistryError:
        log.error("invalid budget rules", exc_info=True)
        return None


async def _close(checker: Checker) -> None:
    if checker.judge.llm is not None:
        await checker.judge.llm.aclose()
    if isinstance(checker.kb, HttpKnowledgeBase):
        await checker.kb.aclose()


def build_app(settings: CriticSettings | None = None, *, checker: Checker | None = None) -> FastAPI:
    settings = settings or CriticSettings()

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
            if app.state.checker is not None:
                await _close(app.state.checker)

    # other services being down does not make this pod unready: requests say so themselves
    app = create_app(
        "critic",
        ready_check=lambda: app.state.checker is not None and app.state.budgets is not None,
        settings=settings,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.tracer = checker.tracer if checker else TraceClient(settings.agentops_url)
    app.state.checker = checker or build_checker(settings, app.state.tracer)
    app.state.budgets = _load_budgets(settings)
    app.include_router(router)
    return app


app = build_app()
