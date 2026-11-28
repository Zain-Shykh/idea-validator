"""Prometheus metrics of architecture §15.1, fed by newly stored records only."""

import re

from prometheus_client import Counter, Histogram

RUNS = Counter("ideaval_runs_total", "Finished runs", ["workflow", "status"])
VERDICT_COST = Histogram(
    "ideaval_verdict_cost_usd",
    "Run cost at the time of each verdict",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
AGENT_LATENCY = Histogram(
    "ideaval_agent_latency_seconds",
    "Model and tool call latency",
    ["agent"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 90),
)
TOKENS = Counter("ideaval_llm_tokens_total", "Model tokens", ["model", "direction"])
CACHE = Counter("ideaval_cache_requests_total", "Cacheable calls (M2-5)", ["cache", "result"])
FALLBACKS = Counter("ideaval_scorer_fallbacks_total", "Scorer fallbacks", ["dimension"])
APPROVALS = Counter("ideaval_approvals_total", "Approval events", ["decision"])
INJECTIONS = Counter("ideaval_injections_blocked_total", "Blocked injections", ["stage"])
BUDGET_STOPS = Counter("ideaval_budget_stops_total", "Runs stopped by their budget")
REJECTED = Counter("ideaval_trace_rejected_total", "Trace records rejected as invalid")
BAD_EVENTS = Counter("ideaval_trace_bad_events_total", "Ops events with an unusable payload")

_LABEL = re.compile(r"[A-Za-z0-9@._:/-]{1,64}")


class BoundedLabel:
    """Producer-supplied label values: odd or too many distinct ones become ``other``."""

    def __init__(self, limit: int = 50) -> None:
        self._seen: set[str] = set()
        self._limit = limit

    def __call__(self, value: str | None) -> str:
        if value is None or not _LABEL.fullmatch(value):
            return "other"
        if value not in self._seen and len(self._seen) >= self._limit:
            return "other"
        self._seen.add(value)
        return value


agent_label = BoundedLabel()
model_label = BoundedLabel()
