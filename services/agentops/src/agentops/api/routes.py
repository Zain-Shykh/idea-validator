"""M13 API (architecture §7.2, §15). Drift and evaluation reports are stubs until Z10."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from agentops.services import metrics
from agentops.services.aggregate import summarise
from agentops.services.ingest import (
    MAX_BYTES,
    IndexNotConfiguredError,
    InvalidBatchError,
    TraceService,
    parse_batch,
)
from common import context
from common.contracts.core import TraceRecord
from common.contracts.ops import OpsSummary, RegistryEntry, RegistryResponse, Report
from common.service import ApiError

log = logging.getLogger("agentops.api")
router = APIRouter()

_TRACE_BODY = {
    "requestBody": {
        "required": True,
        "description": "A batch of common.contracts.core.TraceRecord",
        "content": {"application/json": {"schema": {"type": "array", "items": {"type": "object"}}}},
    }
}


async def _storage[T](call: Callable[[], T]) -> T:
    """Run blocking disk and database work off the event loop; failures are retryable (503)."""
    try:
        return await asyncio.to_thread(call)
    except (OSError, SQLAlchemyError, IndexNotConfiguredError) as exc:
        # the type only: database errors quote the values they were given
        log.error("storage unavailable: %s", type(exc).__name__)
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Trace storage is unavailable") from exc


async def _body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BYTES:
            metrics.REJECTED.inc()
            raise ApiError(413, "PAYLOAD_TOO_LARGE", f"A batch is at most {MAX_BYTES} bytes")
    return bytes(body)


def _service(request: Request) -> TraceService:
    service: TraceService = request.app.state.traces
    return service


# the body is read here, not by FastAPI, so rejected records can be counted and capped
@router.post("/trace", status_code=202, response_class=Response, openapi_extra=_TRACE_BODY)
async def trace(request: Request) -> None:
    body = await _body(request)
    try:
        records = await asyncio.to_thread(parse_batch, body)
    except InvalidBatchError as exc:
        metrics.REJECTED.inc(exc.records)
        log.warning("rejected a trace batch (%d invalid records)", exc.records)
        raise ApiError(
            422, "VALIDATION_ERROR", "Invalid trace records", {"errors": exc.errors}
        ) from exc
    await _storage(lambda: _service(request).ingest(records))


@router.get("/runs/{run_id}/trace")
async def run_trace(run_id: str, request: Request) -> list[TraceRecord]:
    """Ordered by ``(ts, step_id)``; ``parent_step`` gives the tree."""
    if context.safe_id(run_id) is None:
        raise ApiError(422, "VALIDATION_ERROR", "Invalid run id")
    return await _storage(lambda: _service(request).files.read(run_id))


@router.get("/summary")
async def summary(request: Request) -> OpsSummary:
    def build() -> OpsSummary:
        index = _service(request).index
        if index is None:
            raise IndexNotConfiguredError
        return summarise(index.all(), datetime.now(UTC))

    return await _storage(build)


@router.get("/registry")
async def registry(request: Request) -> RegistryResponse:
    entries: list[RegistryEntry] | None = request.app.state.registry
    if entries is None:
        raise ApiError(503, "REGISTRY_INVALID", "The rubric and prompt registry did not load")
    return RegistryResponse(entries=entries)


def _empty_report(kind: Literal["drift", "eval"]) -> Report:
    return Report(kind=kind, git_sha="stub", created_at=datetime.now(UTC), metrics={})


@router.get("/drift/latest")
async def drift_latest() -> Report:
    return _empty_report("drift")


@router.get("/eval/latest")
async def eval_latest() -> Report:
    return _empty_report("eval")
