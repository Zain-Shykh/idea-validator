"""M13 AgenticOps API (architecture §7.2, §15). Owner: Zain.

``POST /trace`` takes a JSON array of ``core.TraceRecord``; ``GET /runs/{id}/trace``
returns the same array in order.

A fact the dashboard needs is a trace record with ``kind="decision"``, ``tool=<OpsEvent>`` and
``output=<payload>.model_dump_json()``. Cost and tokens are summed over all records, so each model
call carries its cost on one record only.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from common.contracts.base import Amount, ContractModel, Count, NonEmptyStr, Unit
from common.contracts.core import Dimension, RunState, VerdictLabel, Versions, Workflow

ApprovalState = Literal["pending", "approved", "declined"]


class OpsEvent(StrEnum):
    RUN_STARTED = "run_started"
    RUN_STATUS = "run_status"
    VERDICT = "verdict"
    APPROVAL = "approval"
    INJECTION_BLOCKED = "injection_blocked"
    SCORER_FALLBACK = "scorer_fallback"


class RunStartedEvent(ContractModel):
    workflow: Workflow
    user_id: NonEmptyStr


class RunStatusEvent(ContractModel):
    status: RunState


class VerdictEvent(ContractModel):
    idea_id: NonEmptyStr
    version: int = Field(ge=1)
    verdict: VerdictLabel
    versions: Versions


class ApprovalEvent(ContractModel):
    approval_id: NonEmptyStr
    decision: ApprovalState


class InjectionBlockedEvent(ContractModel):
    stage: Literal["m5", "m12"]
    count: int = Field(ge=1, le=1_000_000)


class ScorerFallbackEvent(ContractModel):
    dimension: Dimension


OPS_EVENT_PAYLOADS: dict[OpsEvent, type[ContractModel]] = {
    OpsEvent.RUN_STARTED: RunStartedEvent,
    OpsEvent.RUN_STATUS: RunStatusEvent,
    OpsEvent.VERDICT: VerdictEvent,
    OpsEvent.APPROVAL: ApprovalEvent,
    OpsEvent.INJECTION_BLOCKED: InjectionBlockedEvent,
    OpsEvent.SCORER_FALLBACK: ScorerFallbackEvent,
}


class AgentLatency(ContractModel):
    agent: NonEmptyStr
    calls: Count
    avg_ms: Amount
    p95_ms: Amount
    p50_ms: Amount = 0


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
    approvals: dict[ApprovalState, Count]
    blocked_injections: Count
    budget_stops: Count = 0
    runs_by_status: dict[RunState, Count] = Field(default_factory=dict)


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
