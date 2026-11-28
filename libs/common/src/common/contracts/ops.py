"""M13 AgenticOps API (architecture §7.2, §15). Owner: Zain.

``POST /trace`` takes a JSON array of ``core.TraceRecord``; ``GET /runs/{id}/trace``
returns the same array in order.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from common.contracts.base import Amount, ContractModel, Count, NonEmptyStr, Unit
from common.contracts.core import VerdictLabel


class AgentLatency(ContractModel):
    agent: NonEmptyStr
    calls: Count
    avg_ms: Amount
    p95_ms: Amount


class OpsSummary(ContractModel):
    """Dashboard data (AO-6)."""

    generated_at: datetime
    runs_total: Count
    verdicts_total: Count
    verdict_counts: dict[VerdictLabel, Count]
    cost_per_verdict_usd: Amount
    total_cost_usd: Amount
    agent_latency: list[AgentLatency]
    cache_hit_rate: Unit
    scorer_fallbacks: Count
    approvals: dict[Literal["pending", "approved", "declined"], Count]
    blocked_injections: Count
    budget_stops: Count = 0


class RegistryEntry(ContractModel):
    kind: Literal["prompt", "rubric", "config"]
    name: NonEmptyStr
    version: NonEmptyStr
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: NonEmptyStr


class RegistryResponse(ContractModel):
    entries: list[RegistryEntry]


class Report(ContractModel):
    """Drift and evaluation reports; metric names are fixed in Z10."""

    kind: Literal["eval", "retrieval", "drift", "finetune"]
    git_sha: str
    created_at: datetime
    metrics: dict[str, Any]
    path: str | None = None
