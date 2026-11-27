"""The whole orchestrator on a real Redis, configured from the environment as in production.
Needs Redis: docker compose up -d redis (REDIS_HOST_PORT / REDIS_PASSWORD if changed).
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis

from common.contracts.core import RunEvent, RunEventType
from orchestrator.main import build_app
from orchestrator.repositories.events import FIELD, EventStream, stream_key
from orchestrator.services.stub_run import fixture_events, new_id
from orchestrator.settings import OrchestratorSettings
from verdict.main import app as verdict_app

pytestmark = pytest.mark.integration

PORT = int(os.environ.get("REDIS_HOST_PORT", "6379"))
PASSWORD = os.environ.get("REDIS_PASSWORD", "")


def settings(port: int) -> OrchestratorSettings:
    return OrchestratorSettings.model_validate(
        {
            "VERDICT_URL": "http://verdict:8000",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": port,
            "REDIS_PASSWORD": PASSWORD,
        }
    )


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis(host="127.0.0.1", port=PORT, password=PASSWORD or None)
    yield client
    await client.aclose()


async def test_run_through_the_api_writes_the_stream(redis: Redis) -> None:
    app = build_app(settings(PORT), verdict_transport=httpx.ASGITransport(app=verdict_app))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        assert (await client.get("/ready")).status_code == 200
        reply = await client.post(
            "/runs",
            json={
                "workflow": "validate",
                "user_id": "u_1",
                "idea_version_id": "idea_1",
                "profile": {
                    "user_id": "u_1",
                    "skills": [],
                    "hours_per_week": 10,
                    "weeks_available": 12,
                    "team_size": 1,
                    "compute": "laptop",
                    "goal": "portfolio",
                    "interests": [],
                },
            },
        )
        run_id = reply.json()["run_id"]
        try:
            assert (await client.post(f"/runs/{run_id}/resume")).status_code == 202
            stored = await redis.xrange(stream_key(run_id))
            events = [RunEvent.model_validate_json(f[FIELD.encode()]) for _, f in stored]
            assert [e.type for e in events] == [e.type for e in fixture_events()]
            assert RunEventType.VERDICT in {e.type for e in events}
        finally:
            await redis.delete(stream_key(run_id))


async def test_a_lost_xadd_reply_does_not_duplicate_the_event(redis: Redis) -> None:
    """A proxy delivers the first XADD but swallows its reply, as a timeout would. The client
    built by ``build_app`` must not append the same event twice (the gateway reads them all)."""
    drop_next = True
    writers: list[asyncio.StreamWriter] = []

    async def proxy(client_in: asyncio.StreamReader, client_out: asyncio.StreamWriter) -> None:
        redis_in, redis_out = await asyncio.open_connection("127.0.0.1", PORT)
        writers.extend((client_out, redis_out))
        mute = False

        async def upstream() -> None:
            nonlocal drop_next, mute
            while data := await client_in.read(65536):
                if drop_next and b"XADD" in data:
                    drop_next, mute = False, True
                redis_out.write(data)

        async def downstream() -> None:
            while data := await redis_in.read(65536):
                if not mute:
                    client_out.write(data)

        await asyncio.gather(upstream(), downstream(), return_exceptions=True)

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    app = build_app(settings(server.sockets[0].getsockname()[1]))
    events: EventStream = app.state.runner.events
    run_id = new_id("run")
    try:
        # raising is as good as succeeding here; writing twice is not
        with contextlib.suppress(Exception):
            await events.append(fixture_events()[0].model_copy(update={"run_id": run_id}))
        assert not drop_next, "the proxy never saw an XADD"
        assert await redis.xlen(stream_key(run_id)) == 1
    finally:
        await redis.delete(stream_key(run_id))
        server.close()
        for writer in writers:
            writer.close()
