import asyncio
import contextlib
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from common.http import ServiceClient
from common.service import create_app, error_response
from common.trace import TraceClient
from orchestrator.api.routes import router
from orchestrator.repositories.events import EventStream
from orchestrator.services.stub_run import StubRunner
from orchestrator.settings import OrchestratorSettings

REDIS_TIMEOUT_S = 3.0


def build_app(
    settings: OrchestratorSettings | None = None,
    *,
    redis: Redis | None = None,
    verdict_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """``redis`` and ``verdict_transport`` replace the network in tests."""
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
    verdict = (
        ServiceClient(settings.verdict_url, transport=verdict_transport)
        if settings.verdict_url
        else None
    )
    trace = TraceClient(settings.agentops_url)

    async def ready() -> bool:
        await store.ping()
        return verdict is not None

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        flusher = asyncio.create_task(trace.run_periodic_flush())
        try:
            yield
        finally:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
            await trace.aclose()
            if verdict:
                await verdict.aclose()
            await store.aclose()

    app = create_app("orchestrator", ready, settings=settings, lifespan=lifespan)
    app.state.runner = StubRunner(EventStream(store), verdict, trace)
    app.include_router(router)

    @app.exception_handler(RedisError)
    async def on_redis_error(_: Request, __: RedisError) -> JSONResponse:
        return error_response(503, "DEPENDENCY_UNAVAILABLE", "the event store is unavailable")

    return app


app = build_app()
