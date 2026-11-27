"""Needs Redis: docker compose up -d redis (REDIS_HOST_PORT / REDIS_PASSWORD if changed)."""

import os
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from orchestrator.repositories.events import FIELD, TTL_SECONDS, EventStream, stream_key
from orchestrator.services.stub_run import fixture_events, new_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis(
        host="127.0.0.1",
        port=int(os.environ.get("REDIS_HOST_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD") or None,
        decode_responses=True,
    )
    yield client
    await client.aclose()


async def test_events_are_appended_in_order_with_a_ttl(redis: Redis) -> None:
    stream = EventStream(redis)
    run_id = new_id("run")
    assert not await stream.exists(run_id)
    events = [e.model_copy(update={"run_id": run_id}) for e in fixture_events()[:3]]
    try:
        for event in events:
            await stream.append(event)
        stored = await redis.xrange(stream_key(run_id))
        assert [fields[FIELD] for _, fields in stored] == [e.model_dump_json() for e in events]
        assert 0 < await redis.ttl(stream_key(run_id)) <= TTL_SECONDS
        assert await stream.exists(run_id)
    finally:
        await redis.delete(stream_key(run_id))
