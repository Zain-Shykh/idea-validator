"""What a step needs for one run: events (U3), trace records (AO-1, M3-6), traced downstream
calls, spend and the budget check."""

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.exceptions import RedisError

from common import context
from common.contracts.base import ContractModel
from common.contracts.core import (
    PendingInput,
    RunEvent,
    RunEventType,
    RunState,
    TraceKind,
    TraceRecord,
)
from common.contracts.ops import OpsEvent, RunStatusEvent
from common.contracts.profile import RunUpdate
from common.http import ServiceError
from common.trace import TraceClient, new_step_id
from orchestrator.repositories.events import EventStream
from orchestrator.repositories.memo import ActiveRuns, StepMemo
from orchestrator.repositories.profile import ProfileStore
from orchestrator.services import budget
from orchestrator.services.downstream import Peers
from orchestrator.services.results import run_result
from orchestrator.services.rules import Rules
from orchestrator.services.state_machine import Checkpoint, check_run_transition
from orchestrator.settings import OrchestratorSettings

AGENT = "orchestrator"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

log = logging.getLogger("orchestrator")


def new_id(prefix: str) -> str:
    """``<prefix>_<ulid>`` (conventions §7)."""
    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    return prefix + "_" + "".join(_CROCKFORD[(value >> s) & 31] for s in range(125, -1, -5))


class RunFailedError(Exception):
    """The run cannot continue (FAILED)."""

    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


class BudgetStoppedError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class StoreUnavailableError(Exception):
    """M2 failed: nothing can be checkpointed, so the run stops where it is and stays
    resumable."""


async def stored[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except ServiceError as exc:
        raise StoreUnavailableError(exc.summary) from exc


@dataclass
class Deps:
    settings: OrchestratorSettings
    rules: Rules
    peers: Peers
    store: ProfileStore
    events: EventStream
    memo: StepMemo
    active: ActiveRuns
    trace: TraceClient
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    # pause before a repeated scorer call
    retry_wait_s: float = 0.5
    event_locks: dict[str, asyncio.Lock] = field(default_factory=dict)


class RunContext:
    def __init__(self, run_id: str, cp: Checkpoint, deps: Deps) -> None:
        self.run_id = run_id
        self.cp = cp
        self.deps = deps
        # events that must only be seen once the state they announce is stored
        self.after_save: list[Callable[[], Awaitable[None]]] = []

    async def save(self) -> None:
        """Checkpoint to M2 (§6.1)."""
        cp = self.cp
        cp.updated_at = self.deps.clock()
        result = None
        if cp.status is RunState.COMPLETED and cp.verdict is not None:
            result = run_result(cp.verdict).model_dump(mode="json", by_alias=True)
        await stored(
            self.deps.store.update_run(
                self.run_id,
                RunUpdate(
                    status=cp.status,
                    state=cp.model_dump(mode="json", by_alias=True),
                    spend=cp.spend,
                    result=result,
                ),
            )
        )

    # --- events ----------------------------------------------------------------------------

    async def emit(
        self,
        type: RunEventType,
        message: str,
        *,
        agent: str | None = None,
        step: str | None = None,
        data: dict[str, Any] | None = None,
        required: bool = False,
    ) -> None:
        """Best effort unless ``required``: a run goes on when Redis does not."""
        # parallel scorers must not take the same sequence number
        lock = self.deps.event_locks.setdefault(self.run_id, asyncio.Lock())
        try:
            async with lock:
                await self.deps.events.append(
                    RunEvent(
                        run_id=self.run_id,
                        seq=await self.deps.events.next_seq(self.run_id),
                        ts=self.deps.clock(),
                        type=type,
                        agent=agent,
                        step=step,
                        message=message,
                        data=data,
                        spend=self.cp.spend,
                    )
                )
        except (RedisError, ValueError):
            if required:
                raise
            log.error("could not write a run event", exc_info=True, extra={"run_id": self.run_id})

    # --- trace records ---------------------------------------------------------------------

    def decision(self, name: str, facts: Mapping[str, Any]) -> None:
        """An orchestrator decision. These reach stdout unredacted: ids, labels and counts
        only, never text of the idea, the profile or a model."""
        self._record(name, json.dumps(dict(facts), sort_keys=True))

    def ops(self, event: OpsEvent, payload: ContractModel) -> None:
        """A run fact for the M13 dashboard (D-007)."""
        self._record(event, payload.model_dump_json())

    def _record(self, tool: str, output: str) -> None:
        self.deps.trace.emit(
            TraceRecord(
                run_id=self.run_id,
                step_id=new_step_id(),
                parent_step=context.parent_step_var.get(),
                agent=AGENT,
                kind=TraceKind.DECISION,
                tool=tool,
                output=output,
                ts=self.deps.clock(),
            )
        )

    def cache_record(self, tool: str, hit: bool) -> None:
        with self.deps.trace.step(AGENT, TraceKind.TOOL_CALL, tool=tool, run_id=self.run_id) as s:
            s.cache_hit = hit

    async def cache_hit(self, tool: str, agent: str, message: str) -> None:
        self.cache_record(tool, True)
        await self.emit(
            RunEventType.CACHE_HIT, message, agent=agent, step=self.cp.step, data={"tool": tool}
        )

    async def tool_event(self, agent: str, tool: str) -> None:
        await self.emit(
            RunEventType.TOOL_CALL, tool, agent=agent, step=self.cp.step, data={"tool": tool}
        )

    async def call(
        self,
        peer: str,
        path: str,
        *,
        tool: str,
        agent: str,
        response_model: Any,
        summary: Callable[[Any], object],
        body: ContractModel | None = None,
        method: str = "POST",
        retry: bool = True,
        limit_s: float | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        """One traced tool call. ``summary`` is what the trace keeps of the reply: it reaches
        stdout, so ids and labels only. The full request is kept in M13."""
        await self.tool_event(agent, tool)
        with self.deps.trace.step(
            AGENT,
            TraceKind.TOOL_CALL,
            tool=tool,
            input=None if body is None else body.model_dump_json(by_alias=True),
            run_id=self.run_id,
        ) as step:
            reply = await self.deps.peers.request(
                peer,
                method,
                path,
                body=body,
                response_model=response_model,
                retry=retry,
                limit_s=limit_s,
                timeout=None if timeout_s is None else httpx.Timeout(timeout_s, connect=3.0),
            )
            step.output = json.dumps(summary(reply))
            return reply

    # --- spend and budget ------------------------------------------------------------------

    async def sync_spend(self) -> None:
        """Tokens and cost as M13 has them. M13 lags by its flush interval and may be down;
        the last known values are kept then. M5 enforces its own caps (A2)."""
        try:
            records: list[TraceRecord] = await self.deps.peers.request(
                "agentops",
                "GET",
                f"/runs/{self.run_id}/trace",
                response_model=list[TraceRecord],
                retry=False,
            )
        except ServiceError:
            return
        self.cp.spend = self.cp.spend.model_copy(
            update={
                "tokens": max(
                    self.cp.spend.tokens, sum(r.tokens_in + r.tokens_out for r in records)
                ),
                "cost_usd": max(self.cp.spend.cost_usd, sum(r.cost_usd for r in records)),
            }
        )

    def add_spend(self, steps: int = 0, pages: int = 0) -> None:
        spend = self.cp.spend
        self.cp.spend = spend.model_copy(
            update={"steps": spend.steps + steps, "pages": max(0, spend.pages + pages)}
        )

    async def spend_steps(self, steps: int = 1) -> None:
        """Charge ``steps`` agent calls, or stop the run when a cap is used up (M3-4)."""
        await self.sync_spend()
        problems = budget.exhausted(self.cp.spend, self.cp.budget, steps)
        if problems:
            raise BudgetStoppedError(problems)
        self.add_spend(steps=steps)

    # --- run state -------------------------------------------------------------------------

    def set_status(self, status: RunState) -> None:
        check_run_transition(self.cp.status, status)
        self.cp.status = status
        self.ops(OpsEvent.RUN_STATUS, RunStatusEvent(status=status))

    def pause(self, status: RunState, pending: PendingInput) -> None:
        self.set_status(status)
        self.cp.pending = pending
        self.cp.waiting_since = self.deps.clock()
