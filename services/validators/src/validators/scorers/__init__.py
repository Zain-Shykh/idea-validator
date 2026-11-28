"""The four scorers (M9-1). Each has its own queries, feature schema and gate inputs."""

from collections.abc import Awaitable, Callable

from common.contracts.agents import ScorerRequest, ScorerResponse
from validators.scorers import competition, demand, feasibility, risk
from validators.services.engine import Engine

Scorer = Callable[[Engine, ScorerRequest], Awaitable[ScorerResponse]]

SCORERS: dict[str, Scorer] = {
    "demand": demand.run,
    "competition": competition.run,
    "feasibility": feasibility.run,
    "risk": risk.run,
}
