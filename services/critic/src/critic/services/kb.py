"""The two M6 calls the critic needs (§7.2); ``common.fakes.FakeKB`` meets the protocol too."""

from collections.abc import Sequence
from typing import Protocol

import httpx

from common.contracts.core import IdeaCard
from common.contracts.kb import EvidenceRequest, EvidenceResponse
from common.http import ServiceClient, ServiceError


class KnowledgeBase(Protocol):
    async def card(self, card_id: str) -> IdeaCard | None: ...
    async def evidence(self, ids: Sequence[str]) -> EvidenceResponse: ...


class HttpKnowledgeBase:
    def __init__(
        self, url: str, timeout_s: float, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.client = ServiceClient(
            url, timeout=httpx.Timeout(timeout_s, connect=3.0), transport=transport
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

    async def aclose(self) -> None:
        await self.client.aclose()
