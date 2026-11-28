"""M7 Niche and Trend Analyst API (architecture §7.2, §11). Owner: Ahmad (first draft from Z0)."""

from typing import Literal

from pydantic import Field

from common.contracts.base import Amount, ContractModel, Count, EvidenceId, NonEmptyStr
from common.contracts.core import StudentProfile


class Niche(ContractModel):
    niche_id: NonEmptyStr
    name: NonEmptyStr
    size: Count
    trend_slope: float
    trend_label: Literal["rising", "flat", "saturated"]
    match_score: float | None = None


class NicheListResponse(ContractModel):
    """``GET /niches`` and ``POST /niches/match`` (ranked)."""

    niches: list[Niche]


class NicheMatchRequest(ContractModel):
    profile: StudentProfile
    niche_wish: str | None = Field(default=None, max_length=500)


class NicheMonth(ContractModel):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    launches: Count
    mentions: Count


class NicheSeriesResponse(ContractModel):
    niche_id: NonEmptyStr
    series: list[NicheMonth]


class Gap(ContractModel):
    gap_id: NonEmptyStr
    niche_id: NonEmptyStr
    statement: NonEmptyStr
    evidence_ids: list[EvidenceId] = Field(min_length=1)
    score: Amount


class GapsResponse(ContractModel):
    gaps: list[Gap]
