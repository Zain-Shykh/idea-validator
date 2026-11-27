"""M2 (the only store of runs, ideas and verdicts), over HTTP. M2 is not built yet: the paths
M3 needs beyond §7.2's table are proposals (spec Z7 A5), and this is the only file that knows
them. Every id in a path is validated or percent-encoded here."""

from typing import Any
from urllib.parse import quote

from common.contracts.core import Verdict
from common.contracts.profile import (
    Approval,
    ApprovalCreate,
    ApprovalUpdate,
    CachedVerdict,
    Idea,
    IdeaVersion,
    IdeaVersionCreate,
    RunCreate,
    RunRecord,
    RunUpdate,
    VerdictStore,
    VerdictStored,
)
from orchestrator.services.downstream import Peers


def _seg(value: str) -> str:
    return quote(value, safe="")


class ProfileStore:
    def __init__(self, peers: Peers) -> None:
        self.peers = peers

    async def _call[T](
        self, method: str, path: str, *, response_model: type[T], **kwargs: Any
    ) -> T:
        reply: T = await self.peers.request(
            "profile", method, path, response_model=response_model, **kwargs
        )
        return reply

    async def idea_of_version(self, version_id: str) -> Idea:
        return await self._call(
            "GET", f"/internal/ideas/versions/{_seg(version_id)}", response_model=Idea
        )

    async def add_version(self, idea_id: str, body: IdeaVersionCreate, key: str) -> IdeaVersion:
        return await self._call(
            "POST", f"/internal/ideas/{_seg(idea_id)}/versions", body=body,
            response_model=IdeaVersion, idempotency_key=key,
        )  # fmt: skip

    async def create_run(self, body: RunCreate) -> RunRecord:
        return await self._call(
            "POST", "/internal/runs", body=body, response_model=RunRecord,
            idempotency_key=body.run_id,
        )  # fmt: skip

    async def get_run(self, run_id: str) -> RunRecord:
        return await self._call("GET", f"/internal/runs/{_seg(run_id)}", response_model=RunRecord)

    async def update_run(self, run_id: str, body: RunUpdate) -> RunRecord:
        return await self._call(
            "PATCH", f"/internal/runs/{_seg(run_id)}", body=body, response_model=RunRecord
        )

    async def create_approval(self, body: ApprovalCreate) -> Approval:
        return await self._call(
            "POST", "/internal/approvals", body=body, response_model=Approval,
            idempotency_key=body.request.approval_id,
        )  # fmt: skip

    async def decide_approval(self, approval_id: str, body: ApprovalUpdate) -> Approval:
        return await self._call(
            "PATCH", f"/internal/approvals/{_seg(approval_id)}", body=body,
            response_model=Approval,
        )  # fmt: skip

    async def store_verdict(self, body: VerdictStore, key: str) -> VerdictStored:
        return await self._call(
            "POST", "/internal/verdicts", body=body, response_model=VerdictStored,
            idempotency_key=key,
        )  # fmt: skip

    async def cached_verdict(self, cache_key: str) -> CachedVerdict:
        return await self._call(
            "GET", f"/internal/cache/verdicts/{_seg(cache_key)}", response_model=CachedVerdict
        )

    async def cache_verdict(self, cache_key: str, verdict: Verdict) -> None:
        await self.peers.request(
            "profile", "PUT", f"/internal/cache/verdicts/{_seg(cache_key)}", body=verdict
        )
