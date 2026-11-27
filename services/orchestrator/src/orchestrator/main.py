"""M3 orchestrator: the Discover and Validate workflows as checkpointed state machines."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from common.registry import RegistryError
from common.service import create_app, error_response
from common.trace import TraceClient
from orchestrator.api.routes import router
from orchestrator.repositories.events import EventStream
from orchestrator.repositories.memo import ActiveRuns, Proposals, StepMemo
from orchestrator.repositories.profile import ProfileStore
from orchestrator.services.downstream import PEERS, Peers
from orchestrator.services.rules import Rules, load_rules
from orchestrator.services.run import Deps
from orchestrator.services.runner import Runner
from orchestrator.settings import OrchestratorSettings

REDIS_TIMEOUT_S = 3.0
# Kubernetes gives 30 s: uvicorn waits 10 s for open requests (Dockerfile), run tasks get 5 s
# to stop, the last trace flush 8 s
FLUSH_LIMIT_S = 8.0
PROBE_TIMEOUT_S = 2.0
# without these no run can be carried out
REQUIRED_PEERS = ("profile", "verdict", "validators", "kb", "critic", "ideation")
CHECKABLE = frozenset({"profile", "verdict"})

log = logging.getLogger("orchestrator")


def valid_url(value: str | None) -> bool:
    try:
        url = httpx.URL(value or "")
    except httpx.InvalidURL:
        return False
    return url.scheme in {"http", "https"} and bool(url.host)


def _load_rules(settings: OrchestratorSettings) -> Rules | None:
    try:
        return load_rules(
            Path(settings.rubrics_dir),
            Path(settings.prompts_dir),
            Path(settings.budgets_path),
        )
    except RegistryError:
        log.error("invalid rubrics, prompts or budget rules", exc_info=True)
        return None


def config_problems(settings: OrchestratorSettings) -> list[str]:
    """What makes this pod unable to carry out a run, whatever the other services do."""
    problems = [
        f"{PEERS[peer][0].upper()} is missing or not a URL"
        for peer in REQUIRED_PEERS
        if not valid_url(getattr(settings, PEERS[peer][0]))
    ]
    unknown = set(ready_requires(settings)) - CHECKABLE
    if unknown:
        problems.append(f"ORCH_READY_REQUIRES names unknown services: {sorted(unknown)}")
    return problems


def ready_requires(settings: OrchestratorSettings) -> list[str]:
    return [name.strip() for name in settings.ready_requires.split(",") if name.strip()]


def build_app(
    settings: OrchestratorSettings | None = None,
    *,
    redis: Redis | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    tracer: TraceClient | None = None,
    recover: bool = True,
) -> FastAPI:
    """``redis``, ``transport`` (every peer) and ``tracer`` replace the network in tests."""
    settings = settings or OrchestratorSettings()
    password = settings.redis_password.get_secret_value() if settings.redis_password else None
    store = redis or Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password or None,
        socket_timeout=REDIS_TIMEOUT_S,
        socket_connect_timeout=REDIS_TIMEOUT_S,
        # redis-py retries 10 times by default: a slow Redis would hold requests for ~40 s,
        # and a replayed XADD duplicates an event
        retry=Retry(NoBackoff(), 0),
    )
    trace = tracer or TraceClient(settings.agentops_url)
    peers = Peers(settings, transport)
    rules = _load_rules(settings)
    problems = config_problems(settings)
    for problem in problems:
        log.error("%s", problem)

    runner: Runner | None = None
    if rules is not None:
        runner = Runner(
            Deps(
                settings=settings,
                rules=rules,
                peers=peers,
                # a missing PROFILE_URL fails each call like a store that is down
                store=ProfileStore(peers),
                events=EventStream(store),
                memo=StepMemo(store),
                proposals=Proposals(store),
                active=ActiveRuns(store),
                trace=trace,
            )
        )
    probes = Peers(settings, transport)

    async def ready() -> bool:
        await store.ping()
        if runner is None or problems:
            return False
        # a peer's own /ready covers its configuration, not only that it is listening
        await asyncio.gather(
            *(
                probes.client(name).request(
                    "GET", "/ready", retry=False,
                    timeout=httpx.Timeout(PROBE_TIMEOUT_S),
                )
                for name in ready_requires(settings)
            )
        )  # fmt: skip
        return True

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        background = [asyncio.create_task(trace.run_periodic_flush())]
        if runner is not None and recover:
            background.append(asyncio.create_task(runner.recover()))
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            if runner is not None:
                await runner.close()
            try:
                async with asyncio.timeout(FLUSH_LIMIT_S):
                    await trace.aclose()
            except TimeoutError:
                log.error("the last trace records were not delivered in time")
            await peers.aclose()
            await probes.aclose()
            await store.aclose()

    app = create_app("orchestrator", ready, settings=settings, lifespan=lifespan)
    app.state.runner = runner
    app.include_router(router)

    @app.exception_handler(RedisError)
    async def on_redis_error(_: Request, __: RedisError) -> JSONResponse:
        return error_response(503, "DEPENDENCY_UNAVAILABLE", "the event store is unavailable")

    return app


app = build_app()
