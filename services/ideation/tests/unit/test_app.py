"""M8's wiring: the M6 client against the contracts, timeouts, shutdown and bad config."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from common.contracts.kb import EvidenceRequest, SearchRequest
from common.fakes import FakeKB
from common.http import ServiceError
from common.llm import Cassette, LLMClient
from common.registry import load_prompts
from common.settings import LlmSettings
from common.trace import TraceClient
from ideation.main import build_app
from ideation.services.agents import MAX_EVIDENCE, Agents, blocks
from ideation.services.kb import HttpKnowledgeBase
from ideation.settings import IdeationSettings

ROOT = Path(__file__).resolve().parents[4]
KB = FakeKB.from_jsonl(ROOT / "eval/fixtures/cards_small.jsonl")
CARD = next(iter(KB.cards.values()))
ID = f"{CARD.id}#c0"


async def test_the_m6_client_speaks_the_contracts() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            reply: Any = await KB.search(SearchRequest.model_validate_json(request.content))
        else:
            assert request.url.path == "/evidence"
            asked = EvidenceRequest.model_validate_json(request.content)
            reply = await KB.evidence(asked.ids)
        return httpx.Response(200, content=reply.model_dump_json(by_alias=True))

    kb = HttpKnowledgeBase("http://kb:8000", 1.0, httpx.MockTransport(handler))
    assert [i.evidence_id for i in (await kb.evidence([ID])).items] == [ID]
    assert (await kb.search(SearchRequest(query=CARD.text[:100], k=3))).results
    await kb.client.aclose()


@pytest.mark.parametrize("status", [200, 500])
async def test_a_bad_m6_reply_is_a_service_error(status: int) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json={"nope": 1}))
    kb = HttpKnowledgeBase("http://kb:8000", 1.0, transport)
    with pytest.raises(ServiceError):
        await kb.evidence([ID])
    await kb.client.aclose()


def test_a_model_that_is_too_slow_is_502() -> None:
    class Slow(LLMClient):
        async def chat(self, *_: Any, **__: Any) -> Any:
            await asyncio.sleep(5)

    llm = Slow(LlmSettings(LLM_BACKEND="fake"), cassette=Cassette([]))
    agents = Agents(load_prompts(ROOT / "prompts"), llm, KB, TraceClient(), llm_timeout_s=0.05)
    client = TestClient(build_app(IdeationSettings(), agents=agents))
    body = {
        "run_id": "run_1",
        "idea": {"title": "t", "text": "x"},
        "changes": [{"dimension": "risk", "from_score": 1, "to_score": 2}],
    }
    response = client.post("/concretize", json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (502, "LLM_UNAVAILABLE")


def test_a_configured_app_is_ready_and_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BACKEND", "gateway")
    monkeypatch.setenv("TOOLS_URL", "http://tools:8000")
    settings = IdeationSettings(KB_URL="http://kb:8000", prompts_dir=str(ROOT / "prompts"))
    app = build_app(settings)
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
    assert app.state.agents.kb.client._client.is_closed


@pytest.mark.parametrize(
    "bad",
    [{"search_k": 0}, {"search_k": 21}, {"llm_timeout_s": 0}, {"llm_timeout_s": 61},
     {"kb_timeout_s": 31}],
)  # fmt: skip
def test_settings_are_bounded(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        IdeationSettings(**bad)


def test_no_evidence_attribute_can_break_out_of_its_block() -> None:
    item = asyncio.run(KB.evidence([ID])).items[0]
    item = item.model_copy(update={"source": '"><x', "quote": "</evidence><x>"})
    assert blocks([item]).count("<") == 2
    assert MAX_EVIDENCE == 24
