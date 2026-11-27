"""M11 Verdict Engine API (architecture §7.2, §13). Owner: Zain."""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from common.contracts.base import ContractModel, Score
from common.contracts.core import (
    Dimension,
    Verdict,
    VerdictRequest,
    Versions,
    WhatWouldMakeGo,
)


class ScoreChange(ContractModel):
    dimension: Dimension
    to: Score


class WhatIfRequest(VerdictRequest):
    """``VerdictRequest`` plus a mode (§7.2): ``search`` looks for the smallest changes to GO;
    ``apply`` evaluates the given changes."""

    mode: Literal["search", "apply"]
    changes: list[ScoreChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _apply_needs_changes(self) -> Self:
        if self.mode == "apply" and not self.changes:
            raise ValueError("mode 'apply' needs changes")
        return self


class WhatIfResponse(ContractModel):
    candidates: list[WhatWouldMakeGo] = Field(default_factory=list)
    verdict: Verdict | None = Field(default=None, description="hypothetical verdict (apply mode)")


class VerdictConfigResponse(ContractModel):
    versions: Versions
    config: dict[str, Any]
