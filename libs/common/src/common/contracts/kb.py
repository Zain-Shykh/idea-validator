"""M6 Knowledge Base API (architecture §7.2, §9). Owner: Ahmad (first draft from Z0)."""

from datetime import date, datetime
from typing import Any

from pydantic import Field

from common.contracts.base import CardId, ContractModel, Count, EvidenceId, NonEmptyStr
from common.contracts.core import EvidenceItem


class SearchFilters(ContractModel):
    date_from: date | None = None
    date_to: date | None = None
    sources: list[str] | None = None
    niches: list[str] | None = None


class SearchRequest(ContractModel):
    query: NonEmptyStr
    k: int = Field(default=10, ge=1, le=50)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    rerank: bool = True


class SearchResult(EvidenceItem):
    score: float
    card_name: str


class SearchResponse(ContractModel):
    results: list[SearchResult]
    index_version: NonEmptyStr


class EvidenceRequest(ContractModel):
    ids: list[EvidenceId] = Field(min_length=1)


class EvidenceResponse(ContractModel):
    items: list[EvidenceItem]
    missing: list[str] = Field(default_factory=list)


class RawItem(ContractModel):
    source: NonEmptyStr
    external_id: NonEmptyStr
    url: NonEmptyStr
    payload: dict[str, Any]
    fetched_at: datetime


class IngestResponse(ContractModel):
    """``POST /ingest`` takes a JSON array of ``RawItem`` (§7.2)."""

    added: Count
    duplicates: Count
    dropped: Count
    card_ids: list[CardId] = Field(default_factory=list)


class KbStats(ContractModel):
    cards_by_source: dict[str, Count]
    cards_by_niche: dict[str, Count]
    median_age_days: float | None = None
    pct_fresh_90d_by_source: dict[str, float] = Field(default_factory=dict)
    last_ingestion_by_source: dict[str, datetime] = Field(default_factory=dict)
    kb_version: str | None = None
