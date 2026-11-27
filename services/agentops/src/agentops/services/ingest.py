"""``POST /trace``: validate the batch, append it to the run's files, re-index the run."""

import threading
from collections.abc import Sequence
from datetime import UTC

from pydantic import TypeAdapter, ValidationError

from agentops.repositories.files import TraceFiles
from agentops.repositories.index import IndexRepository, TraceIndex
from agentops.services import metrics
from agentops.services.aggregate import CALLS, index_row
from agentops.services.events import BadEventError, parse_event
from common import context
from common.contracts.core import RunState, TraceKind, TraceRecord
from common.contracts.ops import (
    ApprovalEvent,
    InjectionBlockedEvent,
    ScorerFallbackEvent,
    VerdictEvent,
)

MAX_RECORDS = 1000
MAX_BYTES = 5_000_000
# far above any real value, far below what would overflow a BIGINT column when summed
MAX_NUMBER = 10**12
_BATCH = TypeAdapter(list[TraceRecord])
_TERMINAL = {RunState.COMPLETED, RunState.FAILED, RunState.BUDGET_STOPPED, RunState.CANCELLED}


class InvalidBatchError(Exception):
    def __init__(self, records: int, errors: list[dict[str, object]]) -> None:
        super().__init__("invalid trace batch")
        self.records = records
        self.errors = errors[:20]


def _problem(record: TraceRecord) -> str | None:
    """What the contract allows but the files or the database cannot take."""
    if context.safe_id(record.run_id) is None:
        return "run_id must match [A-Za-z0-9._:-]{1,128}"
    if "\x00" in record.agent:
        return "agent must not contain NUL"
    numbers = (record.step_id, record.tokens_in, record.tokens_out, record.latency_ms)
    if max(numbers) > 2**53 or max(numbers[1:]) > MAX_NUMBER or record.cost_usd > MAX_NUMBER:
        return "number out of range"
    if not 2000 <= record.ts.year < 2100:
        return "ts out of range"
    return None


def parse_batch(body: bytes) -> list[TraceRecord]:
    """The whole batch or nothing. Naive timestamps are read as UTC."""
    try:
        records = _BATCH.validate_json(body)
    except ValidationError as exc:
        # never echo submitted values: records hold prompts and student data
        errors: list[dict[str, object]] = [
            {"loc": [str(p) for p in e["loc"]], "type": e["type"], "msg": e["msg"]}
            for e in exc.errors(include_input=False, include_context=False)
        ]
        bad = {e["loc"][0] for e in exc.errors() if e["loc"] and isinstance(e["loc"][0], int)}
        raise InvalidBatchError(max(1, len(bad)), errors) from exc
    if len(records) > MAX_RECORDS:
        raise InvalidBatchError(
            len(records), [{"msg": f"a batch holds at most {MAX_RECORDS} records"}]
        )
    problems: list[dict[str, object]] = [
        {"loc": [str(index)], "msg": problem}
        for index, record in enumerate(records)
        if (problem := _problem(record))
    ]
    if problems:
        raise InvalidBatchError(len(problems), problems)
    return [
        r if r.ts.tzinfo else r.model_copy(update={"ts": r.ts.replace(tzinfo=UTC)}) for r in records
    ]


class TraceService:
    def __init__(self, files: TraceFiles, index: IndexRepository | None) -> None:
        self.files = files
        self.index = index
        # ponytail: one lock for all runs (single replica); per-run locks if ingest gets slow
        self._lock = threading.Lock()

    def ingest(self, records: Sequence[TraceRecord]) -> None:
        """Raises ``OSError`` or ``SQLAlchemyError``; sending the same batch again is safe."""
        runs: dict[str, list[TraceRecord]] = {}
        for record in records:
            runs.setdefault(record.run_id, []).append(record)
        with self._lock:
            for run_id, batch in runs.items():
                self._ingest_run(run_id, batch)

    def _ingest_run(self, run_id: str, batch: list[TraceRecord]) -> None:
        # ponytail: re-reads the run's file per batch (budgets keep runs small)
        stored = self.files.read(run_id)
        self.files.append(run_id, batch)
        known = {r.step_id for r in stored}
        merged = self.files.read(run_id)
        new = [r for r in merged if r.step_id not in known]
        row = index_row(run_id, merged)
        # counted as soon as they are on disk, so a retry after a database error adds nothing
        _observe(new, row, index_row(run_id, stored) if stored else None)
        if self.index is None:
            raise IndexNotConfiguredError
        self.index.upsert(row)


class IndexNotConfiguredError(Exception):
    pass


def _observe(new: Sequence[TraceRecord], row: TraceIndex, before: TraceIndex | None) -> None:
    for record in new:
        if record.kind in CALLS:
            metrics.AGENT_LATENCY.labels(metrics.agent_label(record.agent)).observe(
                record.latency_ms / 1000
            )
            kind = "llm" if record.kind is TraceKind.LLM_CALL else "tool"
            cache = "verdict" if record.tool == "verdict_cache" else kind
            metrics.CACHE.labels(cache, "hit" if record.cache_hit else "miss").inc()
        for direction, tokens in (("in", record.tokens_in), ("out", record.tokens_out)):
            if tokens:
                metrics.TOKENS.labels(metrics.model_label(record.model), direction).inc(tokens)
        try:
            event = parse_event(record)
        except BadEventError:
            metrics.BAD_EVENTS.inc()
            continue
        match event:
            case VerdictEvent():
                metrics.VERDICT_COST.observe(row.cost_usd)
            case ApprovalEvent():
                metrics.APPROVALS.labels(event.decision).inc()
            case InjectionBlockedEvent():
                metrics.INJECTIONS.labels(event.stage).inc(event.count)
            case ScorerFallbackEvent():
                metrics.FALLBACKS.labels(event.dimension.value).inc()
    if row.status in _TERMINAL and (before is None or before.status != row.status):
        metrics.RUNS.labels(row.workflow or "unknown", row.status).inc()
        if row.status == RunState.BUDGET_STOPPED:
            metrics.BUDGET_STOPS.inc()
