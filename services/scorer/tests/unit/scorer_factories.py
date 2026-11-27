"""Builders shared by the scorer tests. No network: the local model is an httpx mock."""

import io
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from common.contracts.agents import ScoreRequest
from common.contracts.core import TraceRecord
from common.llm import Cassette, LLMClient
from common.registry import load_prompts, load_rubrics
from common.settings import LlmSettings
from common.trace import TraceClient
from scorer.services.backend import LocalModel
from scorer.services.scoring import Scorer

ROOT = Path(__file__).resolve().parents[4]
IDS = ["card_a1#c0", "card_a2#c0"]


def evidence(evidence_id: str, quote: str = "Export is slow") -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "card_id": evidence_id.split("#")[0],
        "quote": quote,
        "source": "hackernews",
        "url": "https://example.com/x",
        "retrieved_at": "2026-11-10",
    }


def request(ids: list[str] | None = None, **changes: Any) -> ScoreRequest:
    body = {
        "rubric_id": "demand_pain@v1",
        "idea": {"title": "Slide cards", "text": "Flashcards from slides"},
        "evidence": [evidence(i) for i in (IDS if ids is None else ids)],
        "features": {"n_sources": 2},
    }
    return ScoreRequest.model_validate_json(json.dumps({**body, **changes}))


def reply(ids: list[str] | None = None, **changes: Any) -> str:
    body = {
        "score": 3.5,
        "confidence": 0.7,
        "evidence_ids": IDS if ids is None else ids,
        "reason": "Two sources report it (card_a1#c0).",
    }
    return json.dumps({**body, **changes})


def completion(content: str, tokens_in: int = 500, tokens_out: int = 80) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
        },
    )


Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


class Replies:
    """A mock local server: gives the next reply (or raises it) and keeps the requests."""

    def __init__(self, *replies: str | Exception | httpx.Response) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(req.content))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return completion(item) if isinstance(item, str) else item


class Rig:
    """A Scorer with a mock local model, a cassette as the hosted model and captured traces."""

    def __init__(
        self,
        local: Handler | None,
        hosted: list[str] | None = None,
        *,
        local_timeout_s: float = 5.0,
        model_id: str = "scorer-base@v1",
        hosted_client: LLMClient | None = None,
    ) -> None:
        self.stream = io.StringIO()
        self.sent: list[TraceRecord] = []

        async def sink(batch: list[TraceRecord]) -> None:
            self.sent.extend(batch)

        self.tracer = TraceClient(sink=sink, stream=self.stream, batch_size=1000)
        self.cassette = Cassette(
            [{"alias": "scorer-hosted", "agent": "scorer", "content": c} for c in hosted or []]
        )
        llm = hosted_client or LLMClient(
            LlmSettings(LLM_BACKEND="fake"), tracer=self.tracer, cassette=self.cassette
        )
        _, rubrics = load_rubrics(ROOT / "rubrics")
        self.scorer = Scorer(
            rubrics,
            load_prompts(ROOT / "prompts")["scorer"],
            local=None
            if local is None
            else LocalModel("http://model", "m", timeout_s=5, transport=httpx.MockTransport(local)),
            hosted=llm,
            tracer=self.tracer,
            model_id=model_id,
            local_timeout_s=local_timeout_s,
            hosted_timeout_s=5,
        )

    async def records(self) -> list[TraceRecord]:
        """What M13 would receive."""
        await self.tracer.flush()
        return self.sent
