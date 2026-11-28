"""M5 Tool Layer API (architecture §7.2, §10.1). Owner: Ahmad (first draft from Z0)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from common.contracts.base import Amount, ContractModel, Count, NonEmptyStr


class FetchRequest(ContractModel):
    url: NonEmptyStr
    connector: NonEmptyStr
    run_id: NonEmptyStr
    mode: Literal["json", "text"] = "text"
    max_bytes: int = Field(default=1_000_000, ge=1)


class FetchResponse(ContractModel):
    status: int
    content: str
    sanitised: Literal[True]
    injection_flags: Count
    from_cache: bool
    fetched_at: datetime


class ChatMessage(ContractModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LlmChatRequest(ContractModel):
    run_id: NonEmptyStr
    agent: NonEmptyStr
    model_alias: NonEmptyStr
    messages: list[ChatMessage] = Field(min_length=1)
    response_format: dict[str, Any] | None = None
    max_tokens: int = Field(default=1024, ge=1)
    temperature: float = Field(default=0.0, ge=0, le=2)


class LlmChatResponse(ContractModel):
    content: str
    model: NonEmptyStr
    tokens_in: Count
    tokens_out: Count
    cost_usd: Amount
    latency_ms: Count
    cache_hit: bool
