"""Client side of tracing (M13-1, AO-1, DATA-6).

Every record is written to stdout as one JSON line (K8S-12) and, when ``AGENTOPS_URL`` is set,
POSTed to M13 in batches. Tracing never makes the traced call fail.

Prompts, model replies and tool inputs (search queries carry the student's idea) never reach
stdout, which is widely readable: the line carries only their length and hash; the full record
goes to M13 (replay, AO-1).
"""

import asyncio
import hashlib
import logging
import secrets
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO

from common import context
from common.contracts.core import TraceKind, TraceRecord
from common.http import ServiceClient, ServiceError

Sink = Callable[[list[TraceRecord]], Awaitable[None]]

# M13 will never accept such a batch, so keeping it would block all later records
_REJECTED = frozenset({400, 413, 422})

log = logging.getLogger("common.trace")


_last_step_id = 0


def new_step_id() -> int:
    """Time-ordered, below 2**53 for the UI. Strictly increasing within a process; across pods
    the random part makes a collision unlikely, without a shared counter."""
    global _last_step_id
    candidate = (time.time_ns() // 1_000_000) * 4096 + secrets.randbelow(4096)
    _last_step_id = max(candidate, _last_step_id + 1)
    return _last_step_id


def _digest(text: str | None) -> str | None:
    if text is None:
        return None
    return f"[redacted chars={len(text)} sha256={hashlib.sha256(text.encode()).hexdigest()[:16]}]"


def _stdout_view(record: TraceRecord) -> TraceRecord:
    if record.kind is TraceKind.DECISION:
        return record
    hidden = {"input": _digest(record.input)}
    if record.kind is TraceKind.LLM_CALL:
        hidden["output"] = _digest(record.output)
    return record.model_copy(update=hidden)


def _encodable(record: TraceRecord) -> TraceRecord:
    """A lone surrogate (from a model or a page) cannot be encoded: left in, its batch would
    fail on every delivery and block all later records."""
    cleaned = {
        name: value.encode("utf-8", "replace").decode()
        for name in ("tool", "input", "output", "model", "error")
        if (value := getattr(record, name)) is not None
    }
    return record.model_copy(update=cleaned)


@dataclass
class Step:
    """What the body of ``TraceClient.step()`` can fill in."""

    step_id: int
    output: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    retries: int = 0
    model: str | None = None
    cache_hit: bool = False
    error: str | None = None


class TraceClient:
    def __init__(
        self,
        agentops_url: str | None = None,
        *,
        sink: Sink | None = None,
        batch_size: int = 20,
        max_buffer: int = 1000,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Without ``agentops_url`` and ``sink`` only stdout is written (tests, local runs)."""
        self._client = ServiceClient(agentops_url, retries=1) if agentops_url else None
        self._sink = sink or (self._post if self._client else None)
        self._batch_size = batch_size
        # ponytail: bounded in memory, oldest dropped when M13 stays down
        self._buffer: deque[TraceRecord] = deque(maxlen=max_buffer)
        self._stream = stream
        self._clock = clock
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def _post(self, batch: list[TraceRecord]) -> None:
        assert self._client is not None
        await self._client.post("/trace", batch)

    def emit(self, record: TraceRecord) -> None:
        record = _encodable(record)
        stream = self._stream or sys.stdout
        try:
            stream.write(_stdout_view(record).model_dump_json() + "\n")
            stream.flush()
        except (OSError, ValueError):
            log.warning("could not write trace line", exc_info=True)
        if self._sink is None:
            return
        self._buffer.append(record)
        if len(self._buffer) >= self._batch_size and not self._tasks:
            try:
                task = asyncio.get_running_loop().create_task(self.flush())
            except RuntimeError:
                return  # no event loop: the next flush() sends it
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def flush(self) -> None:
        """Send everything buffered. A batch that was not delivered (failure or cancellation)
        is kept for the next flush, so M13 may see a record twice: ``(run_id, step_id)``
        identifies it. A batch M13 rejects as invalid is dropped."""
        if self._sink is None:
            return
        async with self._lock:
            while self._buffer:
                size = min(self._batch_size, len(self._buffer))
                batch = [self._buffer.popleft() for _ in range(size)]
                try:
                    await self._sink(batch)
                except ServiceError as exc:
                    if exc.status not in _REJECTED:
                        log.warning("trace delivery failed (%d records)", len(batch), exc_info=True)
                        self._requeue(batch)
                        return
                    log.error("M13 rejected %d trace records, dropped", len(batch), exc_info=True)
                except Exception:
                    log.warning("trace delivery failed (%d records)", len(batch), exc_info=True)
                    self._requeue(batch)
                    return
                except BaseException:  # cancelled mid-send, e.g. at shutdown
                    self._requeue(batch)
                    raise

    def _requeue(self, batch: list[TraceRecord]) -> None:
        """Put a batch back in order; if the buffer filled meanwhile, drop the oldest."""
        room = (self._buffer.maxlen or 0) - len(self._buffer)
        if room > 0:
            self._buffer.extendleft(reversed(batch[-room:]))

    async def run_periodic_flush(self, interval_s: float = 2.0) -> None:
        """Run as a background task in a service's lifespan; cancel it, then ``aclose()``."""
        while True:
            await asyncio.sleep(interval_s)
            await self.flush()

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks)
        await self.flush()
        if self._client:
            await self._client.aclose()

    @contextmanager
    def step(
        self,
        agent: str,
        kind: TraceKind,
        *,
        tool: str | None = None,
        input: str | None = None,
        run_id: str | None = None,
        step_id: int | None = None,
        prompt_version: str | None = None,
    ) -> Iterator[Step]:
        """Measure one step and emit its record. Steps and HTTP calls inside become children."""
        run_id = run_id or context.run_id_var.get()
        if not run_id:
            raise ValueError("a trace step needs a run_id (argument or X-Run-Id context)")
        step = Step(step_id=new_step_id() if step_id is None else step_id)
        parent = context.parent_step_var.get()
        token = context.parent_step_var.set(step.step_id)
        started_at = datetime.now(UTC)
        start = self._clock()
        try:
            yield step
        except BaseException as exc:
            # a downstream error message may echo what was sent, so only its code is kept
            detail = exc.summary if isinstance(exc, ServiceError) else str(exc)
            step.error = step.error or f"{type(exc).__name__}: {detail}"[:500]
            raise
        finally:
            context.parent_step_var.reset(token)
            self.emit(
                TraceRecord(
                    run_id=run_id,
                    step_id=step.step_id,
                    parent_step=parent,
                    agent=agent,
                    kind=kind,
                    tool=tool,
                    input=input,
                    output=step.output,
                    tokens_in=step.tokens_in,
                    tokens_out=step.tokens_out,
                    cost_usd=step.cost_usd,
                    latency_ms=max(0, round((self._clock() - start) * 1000)),
                    retries=step.retries,
                    model=step.model,
                    cache_hit=step.cache_hit,
                    error=step.error,
                    ts=started_at,
                    prompt_version=prompt_version,
                )
            )
