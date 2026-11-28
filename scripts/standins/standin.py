"""STAND-IN for M2 (profile), M5 (``/llm/chat``) and M6 (knowledge base). NOT a product service.

Those three are Ahmad's and do not exist yet. This lets a Validate run go through Zain's deployed
services, to find deployment bugs and to load the real scorer model (spec Z11, A6). Everything it
says is made up: its cards are the test fixtures, its "model" is a fixed reply per agent, and it
forgets everything when the pod stops. Nothing measured against it says anything about the real
system. It is never part of ``k8s/``; ``scripts/standins/README.md`` says how to remove it.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Header, Response

from common.contracts.core import Verdict
from common.contracts.kb import (
    EvidenceRequest,
    EvidenceResponse,
    KbStats,
    SearchRequest,
    SearchResponse,
)
from common.contracts.profile import (
    Approval,
    ApprovalCreate,
    ApprovalUpdate,
    CachedVerdict,
    Idea,
    IdeaCreate,
    IdeaVersion,
    IdeaVersionCreate,
    RunCreate,
    RunRecord,
    RunUpdate,
    VerdictStore,
    VerdictStored,
)
from common.contracts.tools import LlmChatRequest, LlmChatResponse
from common.fakes import FakeKB
from common.service import ApiError, create_app

MODEL = "stand-in"
KB_VERSION = "stand-in"
# one fixed, schema-valid reply per agent of a Validate run. Anything else, the hosted scorer
# above all, is refused: a failed local score must abstain, not be answered by a fake
REPLIES: dict[str, dict[str, Any]] = {
    "intake": {
        "slots": {
            "target_user": "students",
            "platform": "web",
            "core_features": [],
            "scope": "mvp",
        },
        "questions": [],
    },
    "validators": {"hours": 120},
    "critic": {"verdict": "supported", "reason": "stand-in reply: nothing was judged"},
    "skeptic": {"objections": []},
    "concretizer": {"changes": []},
}


def build_app(*cards: Path) -> Any:
    kb = FakeKB.from_jsonl(*cards)
    ideas: dict[str, Idea] = {}
    runs: dict[str, RunRecord] = {}
    approvals: dict[str, Approval] = {}
    stored: list[VerdictStore] = []
    cache: dict[str, Verdict] = {}
    seen: dict[str, Any] = {}  # Idempotency-Key -> the first reply
    app = create_app("stand-in")

    def now() -> datetime:
        return datetime.now(UTC)

    def found[T](table: dict[str, T], key: str) -> T:
        if key not in table:
            raise ApiError(404, "NOT_FOUND", "not known to the stand-in")
        return table[key]

    @app.get("/stand-in")
    async def marker() -> dict[str, bool]:
        """How a client can tell: ``scripts/load_validate.py`` marks its report."""
        return {"stand_in": True}

    # --- M2 ----------------------------------------------------------------------------------

    @app.post("/internal/ideas")
    async def add_idea(body: IdeaCreate, idempotency_key: str = Header()) -> Idea:
        if idempotency_key not in seen:
            number = len(ideas) + 1
            version = IdeaVersion(
                id=f"iv_standin{number}", idea_id=f"idea_standin{number}", version=1,
                created_at=now(), text=body.text, origin="initial",
            )  # fmt: skip
            idea = Idea(
                id=version.idea_id, created_at=now(), versions=[version],
                **body.model_dump(exclude={"text"}),
            )  # fmt: skip
            ideas[idea.id] = idea
            seen[idempotency_key] = idea
        reply: Idea = seen[idempotency_key]
        return reply

    @app.get("/internal/ideas/versions/{version_id}")
    async def idea_of_version(version_id: str) -> Idea:
        for idea in ideas.values():
            if any(v.id == version_id for v in idea.versions):
                return idea
        raise ApiError(404, "NOT_FOUND", "not known to the stand-in")

    @app.get("/internal/ideas/{idea_id}")
    async def get_idea(idea_id: str) -> Idea:
        return found(ideas, idea_id)

    @app.post("/internal/ideas/{idea_id}/versions")
    async def add_version(
        idea_id: str, body: IdeaVersionCreate, idempotency_key: str = Header()
    ) -> IdeaVersion:
        idea = found(ideas, idea_id)
        if idempotency_key not in seen:
            number = len(idea.versions) + 1
            version = IdeaVersion(
                **body.model_dump(), id=f"{idea_id}_v{number}", idea_id=idea_id,
                version=number, created_at=now(),
            )  # fmt: skip
            idea.versions.append(version)
            seen[idempotency_key] = version
        reply: IdeaVersion = seen[idempotency_key]
        return reply

    @app.get("/internal/ideas/{idea_id}/versions/{number}/verdict")
    async def verdict_of(idea_id: str, number: int) -> VerdictStore:
        wanted = {v.id for v in found(ideas, idea_id).versions if v.version == number}
        matches = [s for s in stored if s.idea_version_id in wanted]
        if not matches:
            raise ApiError(404, "NOT_FOUND", "no verdict yet")
        return matches[-1]

    @app.get("/internal/profiles/{user_id}")
    async def get_profile(user_id: str) -> None:
        raise ApiError(404, "NOT_FOUND", "the stand-in keeps no profiles")

    @app.post("/internal/runs")
    async def add_run(body: RunCreate) -> RunRecord:
        if body.run_id not in runs:
            runs[body.run_id] = RunRecord(**body.model_dump(), status="QUEUED", created_at=now())
        return runs[body.run_id]

    @app.get("/internal/runs/{run_id}")
    async def get_run(run_id: str) -> RunRecord:
        return found(runs, run_id)

    @app.patch("/internal/runs/{run_id}")
    async def patch_run(run_id: str, body: RunUpdate) -> RunRecord:
        changes = {k: v for k, v in dict(body).items() if v is not None}
        runs[run_id] = found(runs, run_id).model_copy(update=changes)
        return runs[run_id]

    @app.post("/internal/approvals")
    async def add_approval(body: ApprovalCreate) -> Approval:
        item = Approval(**dict(body), decision="pending")
        approvals[body.request.approval_id] = item
        return item

    @app.patch("/internal/approvals/{approval_id}")
    async def decide(approval_id: str, body: ApprovalUpdate) -> Approval:
        item = found(approvals, approval_id).model_copy(update={"decision": body.decision})
        approvals[approval_id] = item
        return item

    @app.post("/internal/verdicts")
    async def store_verdict(body: VerdictStore, idempotency_key: str = Header()) -> VerdictStored:
        if idempotency_key not in seen:
            stored.append(body)
            seen[idempotency_key] = VerdictStored(verdict_id=f"standin_v{len(stored)}")
        reply: VerdictStored = seen[idempotency_key]
        return reply

    @app.get("/internal/cache/verdicts/{key:path}")
    async def cache_get(key: str) -> CachedVerdict:
        return CachedVerdict(hit=key in cache, verdict=cache.get(key))

    @app.put("/internal/cache/verdicts/{key:path}", status_code=204)
    async def cache_put(key: str, body: Verdict) -> Response:
        cache[key] = body
        return Response(status_code=204)

    # --- M6 ----------------------------------------------------------------------------------

    @app.get("/stats")
    async def stats() -> KbStats:
        by_source: dict[str, int] = {}
        for card in kb.cards.values():
            by_source[card.source] = by_source.get(card.source, 0) + 1
        return KbStats(cards_by_source=by_source, cards_by_niche={}, kb_version=KB_VERSION)

    @app.get("/cards/{card_id}")
    async def card(card_id: str) -> Any:
        item = await kb.card(card_id)
        if item is None:
            raise ApiError(404, "NOT_FOUND", "no such card")
        return item.model_dump(mode="json", by_alias=True)

    @app.post("/search")
    async def search(body: SearchRequest) -> SearchResponse:
        return (await kb.search(body)).model_copy(update={"index_version": KB_VERSION})

    @app.post("/evidence")
    async def evidence(body: EvidenceRequest) -> EvidenceResponse:
        return await kb.evidence(body.ids)

    # --- M5 ----------------------------------------------------------------------------------

    @app.post("/llm/chat")
    async def chat(body: LlmChatRequest) -> LlmChatResponse:
        reply = REPLIES.get(body.agent)
        if reply is None or body.model_alias == "scorer-hosted":
            raise ApiError(503, "STAND_IN_HAS_NO_REPLY", "the stand-in has no reply for this")
        return LlmChatResponse(
            content=json.dumps(reply), model=MODEL, tokens_in=0, tokens_out=0,
            cost_usd=0.0, latency_ms=0, cache_hit=False,
        )  # fmt: skip

    return app


if __name__ == "__main__":
    import uvicorn

    cards = sorted(Path(os.environ["STANDIN_CARDS_DIR"]).glob("*.jsonl"))
    uvicorn.run(build_app(*cards), host="0.0.0.0", port=8000)
