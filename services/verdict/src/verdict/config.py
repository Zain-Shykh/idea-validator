"""The rules in ``config/verdict.yaml`` (M11-3). Anything unknown, missing or inconsistent is an
error: a service with bad rules must not be ready, let alone decide.
"""

import math
from pathlib import Path
from typing import Self

import yaml
from pydantic import Field, model_validator

from common.contracts.base import ContractModel, Count, Score, Unit
from common.contracts.core import Dimension, Goal, Thresholds, Weights


class EvidenceGate(ContractModel):
    min_sources: Count
    min_platforms: Count


class FeasibilityGate(ContractModel):
    effort_margin: float = Field(gt=0)


class DuplicateGate(ContractModel):
    differentiation_override: Score


class GateRules(ContractModel):
    evidence: EvidenceGate
    feasibility: FeasibilityGate
    duplicate: DuplicateGate


class ConfidenceRules(ContractModel):
    min: Unit
    w_mean: Unit
    w_sources: Unit
    sources_norm: int = Field(gt=0)

    @model_validator(mode="after")
    def _weights_sum_to_1(self) -> Self:
        if not math.isclose(self.w_mean + self.w_sources, 1, abs_tol=1e-9):
            raise ValueError("w_mean + w_sources must be 1")
        return self


class ChallengeRules(ContractModel):
    score_penalty: Score
    max_penalty: Score
    confidence_penalty: Unit


class SensitivityRules(ContractModel):
    weight_delta: float = Field(ge=0, lt=1)
    score_delta: Score


class WhatIfRules(ContractModel):
    step: float = Field(ge=0.1, le=5)
    max_delta: Score

    @model_validator(mode="after")
    def _at_least_one_step(self) -> Self:
        if self.max_delta < self.step:
            raise ValueError("max_delta must be at least one step")
        return self


class VerdictConfig(ContractModel):
    version: str = Field(pattern=r"^v\d+$")
    weights_version: str = Field(pattern=r"^w\d+$")
    weights: dict[Goal, Weights]
    thresholds: Thresholds
    floor_dimensions: list[Dimension]
    gates: GateRules
    confidence: ConfidenceRules
    challenge: ChallengeRules
    sensitivity: SensitivityRules
    whatif: WhatIfRules

    @model_validator(mode="after")
    def _presets_are_usable(self) -> Self:
        missing = set(Goal) - set(self.weights)
        if missing:
            raise ValueError(f"no weights for goal(s): {', '.join(sorted(missing))}")
        for goal, preset in self.weights.items():
            values = [getattr(preset, d.value) for d in Dimension]
            # stricter than the Weights contract, which has to accept rounded values
            if not math.isclose(math.fsum(values), 100, abs_tol=1e-9):
                raise ValueError(f"{goal} weights must sum to 100, got {math.fsum(values)}")
            # a larger weight cannot be raised by weight_delta without the others going negative
            if max(values) * (1 + self.sensitivity.weight_delta) > 100:
                raise ValueError(f"{goal} has a weight too large for the sensitivity check")
        return self


def load_config(path: str | Path) -> VerdictConfig:
    """Raises ``OSError``, ``yaml.YAMLError`` or ``pydantic.ValidationError``."""
    return VerdictConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
