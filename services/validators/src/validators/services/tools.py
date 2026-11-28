"""The validators' tools: M6 search and card lookup, M7 niches and gaps (§7.2). The protocols
are also met by ``common.fakes``."""

from collections.abc import Sequence
from typing import Protocol

import httpx

from common.contracts.analyst import GapsResponse, NicheListResponse
from common.contracts.core import IdeaCard
from common.contracts.kb import EvidenceRequest, EvidenceResponse, SearchRequest, SearchResponse
from common.http import ServiceClient, ServiceError


class KnowledgeBase(Protocol):
    async def search(self, request: SearchRequest) -> SearchResponse: ...
    async def card(self, card_id: str) -> IdeaCard | None: ...
    async def evidence(self, ids: Sequence[str]) -> EvidenceResponse: ...


class Analyst(Protocol):
    async def niches(self) -> NicheListResponse: ...
    async def gaps(self, niche_id: str) -> GapsResponse: ...


def _client(
    url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None
) -> ServiceClient:
    return ServiceClient(url, timeout=httpx.Timeout(timeout_s, connect=3.0), transport=transport)


class HttpKnowledgeBase:
    def __init__(
        self, url: str, timeout_s: float, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.client = _client(url, timeout_s, transport)

    async def search(self, request: SearchRequest) -> SearchResponse:
        return await self.client.request(
            "POST", "/search", body=request, response_model=SearchResponse
        )

    async def card(self, card_id: str) -> IdeaCard | None:
        try:
            # card ids match CARD_ID_PATTERN (validated by the contracts), so the path is safe
            return await self.client.request("GET", f"/cards/{card_id}", response_model=IdeaCard)
        except ServiceError as exc:
            if exc.status == 404:
                return None
            raise

    async def evidence(self, ids: Sequence[str]) -> EvidenceResponse:
        return await self.client.request(
            "POST", "/evidence", body=EvidenceRequest(ids=list(ids)),
            response_model=EvidenceResponse,
        )  # fmt: skip


class HttpAnalyst:
    def __init__(
        self, url: str, timeout_s: float, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.client = _client(url, timeout_s, transport)

    async def niches(self) -> NicheListResponse:
        return await self.client.request("GET", "/niches", response_model=NicheListResponse)

    async def gaps(self, niche_id: str) -> GapsResponse:
        return await self.client.request(
            "GET", "/gaps", params={"niche_id": niche_id}, response_model=GapsResponse
        )
