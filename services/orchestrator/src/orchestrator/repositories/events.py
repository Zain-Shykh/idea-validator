"""Run progress events (architecture §6.7). The gateway (M1) reads this stream: one entry per
event, with the single field ``event`` holding the ``RunEvent`` JSON.
"""

from redis.asyncio import Redis

from common.contracts.core import RunEvent

MAX_EVENTS = 5000
TTL_SECONDS = 24 * 60 * 60
FIELD = "event"


def stream_key(run_id: str) -> str:
    return f"run:{run_id}:events"


class EventStream:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def append(self, event: RunEvent) -> None:
        key = stream_key(event.run_id)
        await self._redis.xadd(
            key, {FIELD: event.model_dump_json()}, maxlen=MAX_EVENTS, approximate=True
        )
        # renewed on every event: 24 h after the last one, and no stream ever lives forever
        await self._redis.expire(key, TTL_SECONDS)

    async def exists(self, run_id: str) -> bool:
        return bool(await self._redis.exists(stream_key(run_id)))
