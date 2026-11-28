"""Redis keys M3 owns besides the event stream: the step memo (M3-5, §8.4) and the set of runs
to pick up again after a restart. Both are conveniences: when Redis fails, a memo lookup is a
miss and the run goes on."""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from common.contracts.base import ContractModel

MEMO_TTL_S = 24 * 60 * 60
ACTIVE_KEY = "orch:active-runs"
ACTIVE_TTL_S = 7 * 24 * 60 * 60

log = logging.getLogger("orchestrator")


class StepMemo:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get[M: ContractModel](self, key: str, model: type[M]) -> M | None:
        try:
            raw = await self._redis.get(key)
            return None if raw is None else model.model_validate_json(raw)
        except (RedisError, ValueError) as exc:  # ValueError: from another contract version
            # not the message: a validation error quotes the entry
            log.warning("step memo read failed: %s", type(exc).__name__)
            return None

    async def put(self, key: str, value: ContractModel) -> None:
        try:
            await self._redis.set(key, value.model_dump_json(by_alias=True), ex=MEMO_TTL_S)
        except RedisError:
            log.warning("step memo write failed", exc_info=True)


class ActiveRuns:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def add(self, run_id: str) -> None:
        try:
            await self._redis.sadd(ACTIVE_KEY, run_id)
            await self._redis.expire(ACTIVE_KEY, ACTIVE_TTL_S)
        except RedisError:
            log.warning("could not mark the run active", exc_info=True)

    async def remove(self, run_id: str) -> None:
        try:
            await self._redis.srem(ACTIVE_KEY, run_id)
        except RedisError:
            log.warning("could not unmark the run", exc_info=True)

    async def members(self) -> list[str]:
        found = await self._redis.smembers(ACTIVE_KEY)
        return sorted(v.decode() if isinstance(v, bytes) else v for v in found)
