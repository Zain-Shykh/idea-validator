"""What every guard step records (AO-1): decisions with ids and rule names, never text."""

import json
from datetime import UTC, datetime

from common import context
from common.contracts.core import TraceKind, TraceRecord
from common.contracts.ops import InjectionBlockedEvent, OpsEvent
from common.metrics import INJECTIONS_BLOCKED
from common.trace import TraceClient, new_step_id

AGENT = "critic"


def safe(value: str) -> str:
    """A caller-chosen id as it may appear on stdout."""
    return context.safe_id(value) or "(other)"


def record(
    tracer: TraceClient, run_id: str, output: str, tool: str, *, step_input: str | None = None
) -> None:
    tracer.emit(
        TraceRecord(
            run_id=run_id,
            step_id=new_step_id(),
            parent_step=context.parent_step_var.get(),
            agent=AGENT,
            kind=TraceKind.DECISION,
            tool=tool,
            input=step_input,
            output=output,
            ts=datetime.now(UTC),
        )
    )


def decision(tracer: TraceClient, run_id: str, name: str, facts: object) -> None:
    record(tracer, run_id, json.dumps(facts), name)


def injections_blocked(tracer: TraceClient, run_id: str, count: int) -> None:
    if count:
        INJECTIONS_BLOCKED.labels("m12").inc(count)
        event = InjectionBlockedEvent(stage="m12", count=min(count, 1_000_000))
        record(tracer, run_id, event.model_dump_json(), OpsEvent.INJECTION_BLOCKED)
