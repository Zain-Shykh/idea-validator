"""Every shared schema, defined once (GEN-7).

Shared types are in ``core``; one module per owning service for the rest.
Import with ``from common.contracts.core import Verdict``.
"""

from common.contracts import (
    agents,
    analyst,
    core,
    gateway,
    kb,
    ops,
    orchestrator,
    profile,
    scouts,
    tools,
    verdict,
)

__all__ = [
    "agents",
    "analyst",
    "core",
    "gateway",
    "kb",
    "ops",
    "orchestrator",
    "profile",
    "scouts",
    "tools",
    "verdict",
]
