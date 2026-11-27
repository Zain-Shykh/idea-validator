"""The Redis side of M3 on a real Redis: event stream, step memo and active-run set. The other
services are the unit tests' fake stack.
Needs Redis: docker compose up -d redis (REDIS_HOST_PORT / REDIS_PASSWORD if changed).
"""

import os
from collections.abc import AsyncIterator

import pytest
from orchestrator_factories import Orchestrator, Stack, question
from redis.asyncio import Redis

from common.contracts.core import RunEvent, RunEventType
from orchestrator.repositories.events import FIELD, stream_key
from orchestrator.repositories.memo import ACTIVE_KEY

pytestmark = pytest.mark.integration

PORT = int(os.environ.get("REDIS_HOST_PORT", "6379"))
PASSWORD = os.environ.get("REDIS_PASSWORD", "")


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis(host="127.0.0.1", port=PORT, password=PASSWORD or None)
    await client.ping()
    # only this test's keys are removed: the instance may hold other data
    before = set(await client.keys("*"))
    yield client
    if new := set(await client.keys("*")) - before:
        await client.delete(*new)
    await client.aclose()


async def events(redis: Redis, run_id: str) -> list[RunEvent]:
    entries = await redis.xrange(stream_key(run_id))
    return [RunEvent.model_validate_json(fields[FIELD.encode()]) for _, fields in entries]


async def test_run_on_real_redis(redis: Redis) -> None:
    stack = Stack()
    stack.questions = [question()]
    stack.kb_version = f"kb-{os.getpid()}-{os.urandom(4).hex()}"  # no memo from earlier runs
    stack.redis = redis  # type: ignore[assignment]
    first = Orchestrator(stack)
    run_id = await first.run()
    assert first.status(run_id) == "AWAITING_INPUT"
    assert await redis.sismember(ACTIVE_KEY, run_id)

    second = Orchestrator(stack)  # a restart: nothing but M2 and Redis is shared
    stack.scores = {"differentiation": 1.0}
    assert (await second.answer(run_id)).status_code == 202
    assert second.status(run_id) == "COMPLETED"

    stored = await events(redis, run_id)
    assert [e.seq for e in stored] == list(range(len(stored)))
    assert stored[-1].type is RunEventType.RUN_FINISHED
    assert 0 < await redis.ttl(stream_key(run_id)) <= 86400
    assert not await redis.sismember(ACTIVE_KEY, run_id)

    # the memo: the same idea again calls no scorer whose inputs did not change
    stack.questions = []
    stack.cache.clear()
    stack.calls.clear()
    third = await second.run()
    assert second.status(third) == "COMPLETED"
    assert stack.scored() == []
    memo = await redis.keys("cache:step:*")
    assert memo and all([0 < await redis.ttl(k) <= 86400 for k in memo])
