"""The two M6 calls of the gap analyst."""

from collections.abc import Sequence
from typing import Protocol

import httpx

from common.contracts.kb import EvidenceRequest, EvidenceResponse, SearchRequest, SearchResponse
from common.http import ServiceClient


class KnowledgeBase(Protocol):
    async def search(self, request: SearchRequest) -> SearchResponse: ...

    async def evidence(self, ids: Sequence[str]) -> EvidenceResponse: ...


class HttpKnowledgeBase:
    def __init__(
        self, url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.client = ServiceClient(
            url, timeout=httpx.Timeout(timeout_s, connect=3.0), retries=1, transport=transport
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        reply: SearchResponse = await self.client.post(
            "/search", request, response_model=SearchResponse
        )
        return reply

    async def evidence(self, ids: Sequence[str]) -> EvidenceResponse:
        reply: EvidenceResponse = await self.client.post(
            "/evidence", EvidenceRequest(ids=list(ids)), response_model=EvidenceResponse
        )
        return reply
