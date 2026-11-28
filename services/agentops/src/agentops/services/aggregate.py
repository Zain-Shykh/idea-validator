"""Pure arithmetic: a run's records -> its index row; index rows -> the dashboard (AO-6)."""

import math
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta

from agentops.repositories.index import TraceIndex
from agentops.services.events import BadEventError, parse_event
from common.contracts.core import RunState, TraceKind, TraceRecord, VerdictLabel
from common.contracts.ops import (
    AgentLatency,
    ApprovalEvent,
    InjectionBlockedEvent,
    OpsSummary,
    RunStartedEvent,
    RunStatusEvent,
    ScorerFallbackEvent,
    VerdictEvent,
)

# calls that could be served from a cache, and whose latency belongs to an agent
CALLS = frozenset({TraceKind.LLM_CALL, TraceKind.TOOL_CALL})
APPROVAL_STATES = ("pending", "approved", "declined")


def index_row(run_id: str, records: Sequence[TraceRecord]) -> TraceIndex:
    """``records``: the whole run, one per step id, in order. Recomputed from scratch every time,
    so a re-sent batch cannot inflate a total."""
    workflow = user_id = None
    status = RunState.RUNNING.value
    versions: dict[str, object] = {}
    verdicts: list[str] = []
    approvals: dict[str, str] = {}
    latency: dict[str, list[int]] = {}
    cacheable = cache_hits = fallbacks = injections = 0
    for record in records:
        if record.kind in CALLS:
            cacheable += 1
            cache_hits += record.cache_hit
            latency.setdefault(record.agent, []).append(record.latency_ms)
        try:
            event = parse_event(record)
        except BadEventError:
            continue  # counted once at ingest
        match event:
            case RunStartedEvent():
                workflow, user_id = event.workflow.value, event.user_id
            case RunStatusEvent():
                status = event.status.value
            case VerdictEvent():
                verdicts.append(event.verdict.value)
                versions = event.versions.model_dump(mode="json")
            case ApprovalEvent():
                approvals[event.approval_id] = event.decision
            case InjectionBlockedEvent():
                injections += event.count
            case ScorerFallbackEvent():
                fallbacks += 1
    started = min(r.ts for r in records)
    finished = max(r.ts + timedelta(milliseconds=r.latency_ms) for r in records)
    return TraceIndex(
        run_id=run_id,
        workflow=workflow,
        user_id=user_id,
        status=status,
        steps=len(records),
        tokens_in=sum(r.tokens_in for r in records),
        tokens_out=sum(r.tokens_out for r in records),
        cost_usd=math.fsum(r.cost_usd for r in records),
        latency_ms=round((finished - started).total_seconds() * 1000),
        cache_hits=cache_hits,
        fallbacks=fallbacks,
        started_at=started,
        finished_at=finished,
        versions=versions,
        metrics={
            "cacheable": cacheable,
            "verdicts": verdicts,
            "approvals": dict(Counter(approvals.values())),
            "blocked_injections": injections,
            "latency": latency,
        },
    )


def percentile(ordered: Sequence[int], percent: int) -> int:
    """Nearest rank: the smallest value with at least ``percent`` % of values at or below it."""
    return ordered[max(0, math.ceil(percent * len(ordered) / 100) - 1)]


def summarise(rows: Sequence[TraceIndex], now: datetime) -> OpsSummary:
    verdicts: Counter[str] = Counter()
    approvals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    latency: dict[str, list[int]] = {}
    cacheable = cache_hits = fallbacks = injections = 0
    for row in rows:
        statuses[row.status] += 1
        verdicts.update(row.metrics["verdicts"])
        approvals.update(row.metrics["approvals"])
        for agent, values in row.metrics["latency"].items():
            latency.setdefault(agent, []).extend(values)
        cacheable += row.metrics["cacheable"]
        injections += row.metrics["blocked_injections"]
        cache_hits += row.cache_hits
        fallbacks += row.fallbacks
    total_cost = math.fsum(row.cost_usd for row in rows)
    total_verdicts = sum(verdicts.values())
    agents = []
    for agent, values in sorted(latency.items()):
        values.sort()
        agents.append(
            AgentLatency(
                agent=agent,
                calls=len(values),
                avg_ms=sum(values) / len(values),
                p50_ms=percentile(values, 50),
                p95_ms=percentile(values, 95),
            )
        )
    return OpsSummary(
        generated_at=now,
        runs_total=len(rows),
        verdicts_total=total_verdicts,
        verdict_counts={label: verdicts[label.value] for label in VerdictLabel},
        cost_per_verdict_usd=total_cost / total_verdicts if total_verdicts else 0,
        total_cost_usd=total_cost,
        agent_latency=agents,
        cache_hit_rate=cache_hits / cacheable if cacheable else 0,
        scorer_fallbacks=fallbacks,
        approvals={state: approvals[state] for state in APPROVAL_STATES},
        blocked_injections=injections,
        budget_stops=statuses[RunState.BUDGET_STOPPED.value],
        runs_by_status={
            state: statuses[state.value] for state in RunState if statuses[state.value]
        },
    )
