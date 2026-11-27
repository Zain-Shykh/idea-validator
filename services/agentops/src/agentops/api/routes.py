"""M13 stub: trace batches are accepted and discarded (they stay on each pod's stdout); storage,
summary and registry arrive in Z3, drift and evaluation reports in Z10.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Response

from common.contracts.core import TraceRecord, VerdictLabel
from common.contracts.ops import OpsSummary, RegistryResponse, Report

router = APIRouter()


@router.post("/trace", status_code=202, response_class=Response)
async def trace(records: list[TraceRecord]) -> None:
    return None


@router.get("/runs/{run_id}/trace")
async def run_trace(run_id: str) -> list[TraceRecord]:
    return []


@router.get("/summary")
async def summary() -> OpsSummary:
    return OpsSummary(
        generated_at=datetime.now(UTC),
        runs_total=0,
        verdicts_total=0,
        verdict_counts={label: 0 for label in VerdictLabel},
        cost_per_verdict_usd=0,
        total_cost_usd=0,
        agent_latency=[],
        cache_hit_rate=0,
        scorer_fallbacks=0,
        approvals={"pending": 0, "approved": 0, "declined": 0},
        blocked_injections=0,
    )


@router.get("/registry")
async def registry() -> RegistryResponse:
    return RegistryResponse(entries=[])


def _empty_report(kind: Literal["drift", "eval"]) -> Report:
    return Report(kind=kind, git_sha="stub", created_at=datetime.now(UTC), metrics={})


@router.get("/drift/latest")
async def drift_latest() -> Report:
    return _empty_report("drift")


@router.get("/eval/latest")
async def eval_latest() -> Report:
    return _empty_report("eval")
