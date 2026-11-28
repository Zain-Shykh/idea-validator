"""The HTTP clients speak the §7.2 contracts."""

import json

import httpx
import pytest
from validator_factories import GAPS, NICHES, kb

from common.contracts.kb import SearchRequest
from common.http import ServiceError
from validators.services.tools import HttpAnalyst, HttpKnowledgeBase


async def test_kb_client_routes_and_bodies() -> None:
    fake = kb()
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/search":
            found = await fake.search(SearchRequest.model_validate_json(request.content))
        elif request.url.path == "/evidence":
            found = await fake.evidence(json.loads(request.content)["ids"])
        else:
            card = await fake.card(request.url.path.rsplit("/", 1)[1])
            if card is None:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "no"}})
            found = card
        return httpx.Response(200, content=found.model_dump_json())

    client = HttpKnowledgeBase("http://kb:8000", 1, transport=httpx.MockTransport(handler))
    assert (await client.search(SearchRequest(query="lecture slides"))).results
    assert (await client.evidence(["card_816599ab#c0"])).items
    card = await client.card("card_816599ab")
    assert card is not None and card.name == "SlideCards"
    assert await client.card("card_ghost") is None
    assert seen == [("POST", "/search"), ("POST", "/evidence"),
                    ("GET", "/cards/card_816599ab"), ("GET", "/cards/card_ghost")]  # fmt: skip


async def test_card_error_other_than_404_is_raised() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(400, json={}))
    client = HttpKnowledgeBase("http://kb:8000", 1, transport=transport)
    with pytest.raises(ServiceError):
        await client.card("card_a")


async def test_analyst_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/niches":
            return httpx.Response(200, json={"niches": [n.model_dump() for n in NICHES]})
        assert dict(request.url.params) == {"niche_id": "education"}
        return httpx.Response(200, json={"gaps": [g.model_dump() for g in GAPS]})

    client = HttpAnalyst("http://analyst:8000", 1, transport=httpx.MockTransport(handler))
    assert (await client.niches()).niches == NICHES
    assert (await client.gaps("education")).gaps == GAPS
