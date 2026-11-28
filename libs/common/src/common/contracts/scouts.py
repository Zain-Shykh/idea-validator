"""M4 Scouts API (architecture §7.2, §10.2). Owner: Ahmad (first draft from Z0)."""

from pydantic import Field

from common.contracts.base import CardId, ContractModel, Count, NonEmptyStr


class ScoutRequest(ContractModel):
    queries: list[NonEmptyStr] = Field(min_length=1)
    sources: list[NonEmptyStr] = Field(min_length=1)
    page_budget: int = Field(ge=1)
    run_id: NonEmptyStr


class ScoutResponse(ContractModel):
    fetched: Count
    ingested_card_ids: list[CardId]
