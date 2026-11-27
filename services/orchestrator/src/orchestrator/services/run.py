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
from weakref import WeakValueDictionary

import httpx
from redis.exceptions import RedisError

from common import context
from common.contracts.base import ContractModel
from common.contracts.core import (
    Budget,
    PendingInput,
    RunEvent,
    RunEventType,
    RunState,
    Spend,
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
from orchestrator.services.results import result_of
from orchestrator.services.rules import Rules
from orchestrator.services.state_machine import (
    BaseCheckpoint,
    Child,
    DiscoverCheckpoint,
    check_run_transition,
)
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
        # not chained: M2's message may echo the checkpoint, which holds student text
        raise StoreUnavailableError(exc.summary) from None


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
    event_locks: WeakValueDictionary[str, asyncio.Lock] = field(default_factory=WeakValueDictionary)


# a child's own start and end are not the parent's
_NOT_MIRRORED = frozenset({RunEventType.RUN_STARTED, RunEventType.RUN_FINISHED})


class RunContext[C: BaseCheckpoint]:
    def __init__(
        self,
        run_id: str,
        cp: C,
        deps: Deps,
        *,
        parent: "tuple[RunContext[DiscoverCheckpoint], Child] | None" = None,
    ) -> None:
        """``parent``: this is a child of a Discover run and shares its budget."""
        self.run_id = run_id
        self.cp = cp
        self.deps = deps
        self.parent = parent
        # drives another run to its end; set by the runner, used by VALIDATE_EACH
        self.drive: Callable[[RunContext[Any]], Awaitable[None]] | None = None
        # events that must only be seen once the state they announce is stored
        self.after_save: list[Callable[[], Awaitable[None]]] = []
        self.stored_status = cp.status

    async def save(self) -> None:
        """Checkpoint to M2 (§6.1)."""
        cp = self.cp
        cp.updated_at = self.deps.clock()
        done = result_of(cp)
        result = None if done is None else done.model_dump(mode="json", by_alias=True)
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
        # M13 only hears of states that M2 has
        if cp.status is not self.stored_status:
            self.stored_status = cp.status
            self.ops(OpsEvent.RUN_STATUS, RunStatusEvent(status=cp.status))

    @property
    def step(self) -> str | None:
        """The workflow step the run is in."""
        return getattr(self.cp, "step", None)

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
        lock = self.deps.event_locks.get(self.run_id)
        if lock is None:
            self.deps.event_locks[self.run_id] = lock = asyncio.Lock()
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
        except (RedisError, ValueError) as exc:
            if required:
                raise
            log.error(
                "could not write a run event: %s", exc.__class__.__name__,
                extra={"run_id": self.run_id},
            )  # fmt: skip
        if self.parent is not None and type not in _NOT_MIRRORED:
            await self.parent[0].emit(
                type, message, agent=agent, step=step,
                data={**(data or {}), "child_run_id": self.run_id},
            )  # fmt: skip

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
            RunEventType.CACHE_HIT, message, agent=agent, step=self.step, data={"tool": tool}
        )

    async def tool_event(self, agent: str, tool: str) -> None:
        await self.emit(
            RunEventType.TOOL_CALL, tool, agent=agent, step=self.step, data={"tool": tool}
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
        own = self.cp.own_spend()
        self._set_own_spend(
            own.model_copy(
                update={
                    "tokens": max(own.tokens, sum(r.tokens_in + r.tokens_out for r in records)),
                    "cost_usd": max(own.cost_usd, sum(r.cost_usd for r in records)),
                }
            )
        )

    def _set_own_spend(self, spend: Spend) -> None:
        self.cp.set_own_spend(spend)
        if self.parent is not None:
            parent, child = self.parent
            child.spend = spend
            parent.cp.total_spend()

    def add_spend(self, steps: int = 0, pages: int = 0) -> None:
        own = self.cp.own_spend()
        self._set_own_spend(
            own.model_copy(update={"steps": own.steps + steps, "pages": max(0, own.pages + pages)})
        )

    def budget_problems(self, steps: int = 1) -> list[str]:
        """A child's step must fit its own budget and what its parent has left."""
        problems = budget.exhausted(self.cp.spend, self.cp.budget, steps)
        if self.parent is not None:
            shared = self.parent[0].cp
            problems += [
                f"shared {p}" for p in budget.exhausted(shared.spend, shared.budget, steps)
            ]
        return problems

    def remaining(self) -> Budget:
        left = budget.remaining(self.cp.spend, self.cp.budget)
        if self.parent is None:
            return left
        shared = budget.remaining(self.parent[0].cp.spend, self.parent[0].cp.budget)
        return Budget(
            **{
                name: min(getattr(left, name), getattr(shared, name))
                for name in Budget.model_fields
            }
        )

    async def spend_steps(self, steps: int = 1) -> None:
        """Charge ``steps`` agent calls, or stop the run when a cap is used up (M3-4)."""
        await self.sync_spend()
        # nothing awaits between the check and the charge: two children cannot both pass it
        problems = self.budget_problems(steps)
        if problems:
            raise BudgetStoppedError(problems)
        self.add_spend(steps=steps)

    # --- run state -------------------------------------------------------------------------

    def set_status(self, status: RunState) -> None:
        check_run_transition(self.cp.status, status)
        self.cp.status = status

    def pause(self, status: RunState, pending: PendingInput) -> None:
        self.set_status(status)
        self.cp.pending = pending
        self.cp.waiting_since = self.deps.clock()
