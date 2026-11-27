"""Test builders for M3: an in-memory Redis and a fake stack behind one httpx transport.

M11 is the real app. M2, M4, M6, M7, M8, M9, M12 and M13 are fakes that validate every request
against ``common.contracts``, so a body M3 gets wrong fails the test.
"""

import asyncio
import builtins
import io
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError

from common.contracts.agents import (
    ApprovalPolicyRequest,
    CheckRequest,
    CheckResponse,
    ClaimResult,
    ConcreteChange,
    ConcretizeRequest,
    ConcretizeResponse,
    EnrichedIdea,
    FlaggedEvidence,
    GapAnalyzeRequest,
    GapAnalyzeResponse,
    IdeaSlots,
    IdeateRequest,
    IdeateResponse,
    InputRailRequest,
    InputRailResponse,
    IntakeRequest,
    IntakeResponse,
    PartialGateInputs,
    ScanRequest,
    ScanResponse,
    ScorerRequest,
    ScorerResponse,
    SkepticRequest,
    SkepticResponse,
)
from common.contracts.analyst import Gap, Niche, NicheMatchRequest
from common.contracts.core import (
    Dimension,
    DimensionScore,
    IdeaCard,
    RunEvent,
    RunEventType,
    StudentProfile,
    TraceKind,
    TraceRecord,
    Verdict,
)
from common.contracts.kb import EvidenceRequest, KbStats, SearchRequest
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
from common.contracts.scouts import ScoutRequest, ScoutResponse
from common.fakes import FakeAnalyst, FakeKB
from common.injection import scan_text
from common.registry import load_budgets
from common.trace import TraceClient
from critic.services.policy import approval as approval_rule
from orchestrator.main import build_app
from orchestrator.settings import OrchestratorSettings
from verdict.main import app as verdict_app

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "eval/fixtures"
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
USER = "u_1"
HEADERS = {"X-User-Id": USER}
SCORER_MODEL = "scorer-base@v1"
BUDGETS = load_budgets(ROOT / "config/budgets.yaml")
DIMENSIONS = {
    "demand": ("demand_pain",),
    "competition": ("competition", "differentiation"),
    "feasibility": ("feasibility", "learning"),
    "risk": ("risk",),
}
IDEA_TITLE = "Flashcards from lecture decks"
IDEA_TEXT = (
    "A web app that turns a student's lecture slides into flashcards. University students "
    "retype cards from slides by hand today and diagrams get lost on export."
)


def fixture_cards() -> list[IdeaCard]:
    return list(
        FakeKB.from_jsonl(
            FIXTURES / "cards_small.jsonl", FIXTURES / "validator_cards.jsonl"
        ).cards.values()
    )


def card_named(name: str) -> IdeaCard:
    return next(c for c in fixture_cards() if c.name == name)


def evidence_of(name: str) -> str:
    return f"{card_named(name).id}#c0"


GAP_EVIDENCE = evidence_of("Ask HN: flashcards")
NICHE = Niche(
    niche_id="education", name="Education", size=10, trend_slope=0.1, trend_label="rising"
)
GAP = Gap(
    gap_id="g1",
    niche_id="education",
    statement="Making flashcards from slides is manual",
    evidence_ids=[GAP_EVIDENCE, evidence_of("Anki export issue")],
    score=3,
)


def idea(title: str = "Diagram cards", text: str | None = None, **changes: Any) -> dict:
    """A generated idea small enough for any fixture profile and like no fixture product."""
    values = {
        "title": title,
        "text": text or f"{title}: a web page that keeps pictures when making revision cards.",
        "niche_id": "education",
        "target_user": "medical students",
        "gap_evidence_ids": [GAP_EVIDENCE],
        "rationale": "people retype cards by hand",
    }
    return values | changes


def objection(dimension: str = "differentiation", fatal: bool = True, **changes: Any) -> dict:
    values = {
        "objection_id": "x",
        "dimension": dimension,
        "text": f"weak {dimension.replace('_', ' ')}",
        "evidence_ids": [GAP_EVIDENCE],
        "fatal": fatal,
    }
    return values | changes


def profile(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "user_id": USER,
        "skills": ["Python", "React"],
        "hours_per_week": 10,
        "weeks_available": 12,
        "team_size": 2,
        "compute": "laptop",
        "goal": "portfolio",
        "interests": ["education"],
        "version": 1,
    }
    return values | changes


def question(qid: str = "q1", dimension: str = "differentiation", low: float = -3) -> dict:
    """With the default scores (4.0 everywhere, GO) a large drop of one dimension flips."""
    return {
        "question_id": qid,
        "text": f"Is {dimension.replace('_', ' ')} narrow or broad? ({qid})",
        "affects": [dimension],
        "answers": [
            {"answer": "narrow", "score_deltas": {dimension: 0.5}},
            {"answer": "broad", "score_deltas": {dimension: low}},
        ],
    }


class FakeRedis:
    """The commands the service uses, in memory."""

    def __init__(self) -> None:
        self.streams: dict[str, list[dict[str, str]]] = {}
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttl: dict[str, int] = {}
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise RedisConnectionError("down")

    async def ping(self) -> bool:
        self._check()
        return True

    def pipeline(self, **_: Any) -> "FakePipeline":
        return FakePipeline(self)

    async def xadd(self, key: str, fields: dict[str, str], **_: Any) -> None:
        self._check()
        self.streams.setdefault(key, []).append(fields)

    async def xrevrange(self, key: str, count: int) -> list[tuple[str, dict[str, str]]]:
        self._check()
        return [("0-0", fields) for fields in self.streams.get(key, [])[::-1][:count]]

    async def expire(self, key: str, seconds: int) -> None:
        self._check()
        self.ttl[key] = seconds

    async def get(self, key: str) -> str | None:
        self._check()
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self._check()
        self.values[key] = value
        self.ttl[key] = ex

    async def sadd(self, key: str, *members: str) -> None:
        self._check()
        self.sets.setdefault(key, set()).update(members)

    async def srem(self, key: str, *members: str) -> None:
        self._check()
        self.sets.get(key, set()).difference_update(members)

    async def smembers(self, key: str) -> "builtins.set[str]":
        self._check()
        return builtins.set(self.sets.get(key, ()))

    async def aclose(self) -> None:
        pass

    def events(self, run_id: str) -> list[RunEvent]:
        return [
            RunEvent.model_validate_json(e["event"]) for e in self.streams[f"run:{run_id}:events"]
        ]


class FakePipeline:
    """Queues commands and runs them on ``execute``, like the real transaction."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def xadd(self, *args: Any, **kwargs: Any) -> None:
        self.queued.append(("xadd", args, kwargs))

    def expire(self, *args: Any) -> None:
        self.queued.append(("expire", args, {}))

    async def execute(self) -> None:
        for name, args, kwargs in self.queued:
            await getattr(self.redis, name)(*args, **kwargs)


Override = Callable[[httpx.Request], Awaitable[httpx.Response] | httpx.Response | None]


def _reply(model: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=model.model_dump_json(by_alias=True),
        headers={"content-type": "application/json"},
    )


def error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": code}})


class Stack(httpx.AsyncBaseTransport):
    def __init__(self, cards: list[IdeaCard] | None = None) -> None:
        self.kb = FakeKB(fixture_cards() if cards is None else cards)
        self.kb_version: str | None = "kb-1"
        self.redis = FakeRedis()
        self.records: list[TraceRecord] = []
        self.agent_records: list[TraceRecord] = []  # what other services put in M13
        self.calls: list[tuple[str, str]] = []
        self.bodies: dict[str, list[bytes]] = {}
        self.overrides: dict[tuple[str, str], Override] = {}
        self.verdict_asgi = httpx.ASGITransport(app=verdict_app)
        # M2
        self.ideas: dict[str, Idea] = {}
        self.runs: dict[str, RunRecord] = {}
        self.approvals: dict[str, Approval] = {}
        self.stored: list[VerdictStore] = []
        self.cache: dict[str, Verdict] = {}
        # M8, M9, M4
        self.questions: list[dict] = []
        self.objections: list[dict] = []
        self.check_verdicts: dict[str, tuple[str, str]] = {}
        self.scores: dict[str, float] = {}
        self.score_model = SCORER_MODEL
        self.n_sources, self.n_platforms = 4, 3
        self.effort = 100.0
        self.scout_cards: list[IdeaCard] = []
        self.scorer_delay_s = 0.0
        self.in_flight = 0
        self.most_in_flight = 0
        # M7 and the ideation agents of Discover: one reply per debate round (the last repeats)
        self.analyst = FakeAnalyst([NICHE], [GAP])
        self.idea_rounds: list[list[dict]] = [[idea()]]
        self.profiles: dict[str, StudentProfile] = {}
        self.idea_keys: dict[str, str] = {}
        self.add_idea()

    # --- helpers -------------------------------------------------------------------------------

    def add_idea(self, version_id: str = "iv_1", text: str = IDEA_TEXT, user: str = USER) -> None:
        idea_id = "idea_1" if version_id == "iv_1" else f"idea_{version_id}"
        version = IdeaVersion(
            id=version_id, idea_id=idea_id, version=1, created_at=NOW, text=text, origin="initial"
        )
        self.ideas[idea_id] = Idea(
            id=idea_id,
            user_id=user,
            title=IDEA_TITLE,
            source="own",
            created_at=NOW,
            versions=[version],
        )

    def paths(self, host: str) -> list[str]:
        return [path for called, path in self.calls if called == host]

    def scored(self) -> list[str]:
        return [p.removeprefix("/score/") for p in self.paths("validators")]

    def sent[M](self, key: str, model: type[M]) -> list[M]:
        return [model.model_validate_json(b) for b in self.bodies.get(key, [])]  # type: ignore[attr-defined]

    # --- transport -----------------------------------------------------------------------------

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append((host, path))
        self.bodies.setdefault(f"{host}{path}", []).append(request.content)
        for (wanted, prefix), override in self.overrides.items():
            if wanted == host and path.startswith(prefix):
                answer = override(request)
                if asyncio.iscoroutine(answer):
                    answer = await answer
                if answer is not None:
                    return answer  # type: ignore[return-value]
        if path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if host == "verdict":
            return await self.verdict_asgi.handle_async_request(request)
        return await getattr(self, f"_{host}")(request, path)

    async def _profile(self, request: httpx.Request, path: str) -> httpx.Response:
        raw = unquote(request.url.raw_path.decode().split("?")[0])
        parts = raw.strip("/").split("/")[1:]
        body, method = request.content, request.method
        if parts[:2] == ["ideas", "versions"]:
            for idea in self.ideas.values():
                if any(v.id == parts[2] for v in idea.versions):
                    return _reply(idea)
            return error(404, "NOT_FOUND")
        if parts == ["ideas"] and method == "POST":
            new_idea = IdeaCreate.model_validate_json(body)
            key = request.headers["Idempotency-Key"]
            if key not in self.idea_keys:
                number = len(self.idea_keys) + 1
                idea_id = f"idea_gen{number}"
                version = IdeaVersion(
                    id=f"iv_gen{number}", idea_id=idea_id, version=1, created_at=NOW,
                    text=new_idea.text, origin="initial",
                )  # fmt: skip
                self.ideas[idea_id] = Idea(
                    id=idea_id, created_at=NOW, versions=[version],
                    **new_idea.model_dump(exclude={"text"}),
                )  # fmt: skip
                self.idea_keys[key] = idea_id
            return _reply(self.ideas[self.idea_keys[key]])
        if parts[0] == "ideas" and method == "GET":
            if parts[1] not in self.ideas:
                return error(404, "NOT_FOUND")
            if len(parts) == 2:
                return _reply(self.ideas[parts[1]])
            version_id = next(
                (v.id for v in self.ideas[parts[1]].versions if str(v.version) == parts[3]), None
            )
            found_verdicts = [s for s in self.stored if s.idea_version_id == version_id]
            return _reply(found_verdicts[-1]) if found_verdicts else error(404, "NOT_FOUND")
        if parts[0] == "profiles":
            found_profile = self.profiles.get(parts[1])
            return _reply(found_profile) if found_profile else error(404, "NOT_FOUND")
        if parts[0] == "ideas" and method == "POST":
            create = IdeaVersionCreate.model_validate_json(body)
            idea = self.ideas[parts[1]]
            version = IdeaVersion(
                **create.model_dump(),
                id=f"{idea.id}_v{len(idea.versions) + 1}",
                idea_id=idea.id,
                version=len(idea.versions) + 1,
                created_at=NOW,
            )
            idea.versions.append(version)
            return _reply(version)
        if parts[0] == "runs":
            if method == "POST":
                created = RunCreate.model_validate_json(body)
                if created.run_id in self.runs:  # idempotent by run id
                    return _reply(self.runs[created.run_id])
                record = RunRecord(**created.model_dump(), status="QUEUED", created_at=NOW)
                self.runs[created.run_id] = record
                return _reply(record)
            if parts[1] not in self.runs:
                return error(404, "NOT_FOUND")
            if method == "PATCH":
                update = RunUpdate.model_validate_json(body)
                self.runs[parts[1]] = self.runs[parts[1]].model_copy(
                    update={k: v for k, v in dict(update).items() if v is not None}
                )
            return _reply(self.runs[parts[1]])
        if parts[0] == "approvals":
            if method == "POST":
                new = ApprovalCreate.model_validate_json(body)
                item = Approval(**dict(new), decision="pending")
                self.approvals[new.request.approval_id] = item
                return _reply(item)
            decision = ApprovalUpdate.model_validate_json(body).decision
            self.approvals[parts[1]] = self.approvals[parts[1]].model_copy(
                update={"decision": decision}
            )
            return _reply(self.approvals[parts[1]])
        if parts[0] == "verdicts":
            self.stored.append(VerdictStore.model_validate_json(body))
            return _reply(VerdictStored(verdict_id=f"v{len(self.stored)}"))
        assert parts[:2] == ["cache", "verdicts"]
        key = "/".join(parts[2:])
        if method == "PUT":
            self.cache[key] = Verdict.model_validate_json(body)
            return httpx.Response(204)
        found = self.cache.get(key)
        return _reply(CachedVerdict(hit=found is not None, verdict=found))

    async def _kb(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/stats":
            stats = KbStats(cards_by_source={}, cards_by_niche={}, kb_version=self.kb_version)
            return _reply(stats)
        if path.startswith("/cards/"):
            card = await self.kb.card(unquote(path.removeprefix("/cards/")))
            return error(404, "NOT_FOUND") if card is None else _reply(card)
        if path == "/search":
            return _reply(await self.kb.search(SearchRequest.model_validate_json(request.content)))
        assert path == "/evidence", path
        asked = EvidenceRequest.model_validate_json(request.content)
        return _reply(await self.kb.evidence(asked.ids))

    async def _critic(self, request: httpx.Request, path: str) -> httpx.Response:
        body = request.content
        if path == "/rail/input":
            text = InputRailRequest.model_validate_json(body).text
            allowed = "REJECT" not in text
            return _reply(
                InputRailResponse(
                    allowed=allowed,
                    reasons=[] if allowed else ["refused"],
                    redacted_text=text.replace("me@example.com", "[email removed]"),
                )
            )
        if path == "/scan":
            flagged = [
                FlaggedEvidence(evidence_id=i.evidence_id, card_id=i.card_id, patterns=p)
                for i in ScanRequest.model_validate_json(body).evidence
                if (p := scan_text(i.quote, i.source))
            ]
            return _reply(ScanResponse(flagged=flagged))
        if path == "/policy/approval":
            return _reply(approval_rule(ApprovalPolicyRequest.model_validate_json(body), BUDGETS))
        assert path == "/check", path
        results = []
        for claim in CheckRequest.model_validate_json(body).claims:
            verdict, reason = self.check_verdicts.get(claim.claim_id, ("supported", "ok"))
            results.append(
                ClaimResult(
                    claim_id=claim.claim_id,
                    supported=verdict == "supported",
                    verdict=verdict,
                    reason=reason,
                )
            )
        return _reply(CheckResponse(results=results))

    async def _analyst(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/niches/match":
            NicheMatchRequest.model_validate_json(request.content)
            return _reply(await self.analyst.niches())
        assert path == "/gaps", path
        return _reply(await self.analyst.gaps(request.url.params["niche_id"]))

    async def _ideation(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/ideate":
            number = IdeateRequest.model_validate_json(request.content).round
            ideas = self.idea_rounds[min(number, len(self.idea_rounds)) - 1]
            return _reply(IdeateResponse.model_validate({"ideas": ideas}))
        if path == "/gap-analyze":
            wanted = GapAnalyzeRequest.model_validate_json(request.content)
            return _reply(GapAnalyzeResponse(ideas=[EnrichedIdea(idea=i) for i in wanted.ideas]))
        if path == "/intake":
            IntakeRequest.model_validate_json(request.content)
            slots = IdeaSlots(target_user="", platform="", core_features=[], scope="")
            return _reply(
                IntakeResponse.model_validate({"slots": slots, "questions": self.questions})
            )
        if path == "/skeptic":
            SkepticRequest.model_validate_json(request.content)
            return _reply(SkepticResponse.model_validate({"objections": self.objections}))
        assert path == "/concretize", path
        asked = ConcretizeRequest.model_validate_json(request.content)
        return _reply(
            ConcretizeResponse(
                changes=[
                    ConcreteChange(dimension=c.dimension, change=f"narrow the {c.dimension.value}")
                    for c in asked.changes
                ]
            )
        )

    def scorer_response(self, name: str, body: ScorerRequest) -> ScorerResponse:
        evidence_id = (
            body.evidence[0].evidence_id if body.evidence else f"{next(iter(self.kb.cards))}#c0"
        )
        features = {"effort_source": "model"} if name == "feasibility" else {}
        scores = [
            DimensionScore(
                dimension=Dimension(d),
                score=self.scores.get(d, 4.0),
                confidence=0.8,
                evidence_ids=[evidence_id],
                features=features,
                reason=f"{d} scored by code from {evidence_id}",
                rubric_version=f"{d}@v1",
                model=self.score_model,
            )
            for d in DIMENSIONS[name]
        ]
        gates = PartialGateInputs()
        if name == "demand":
            gates = PartialGateInputs(n_sources=self.n_sources, n_platforms=self.n_platforms)
        if name == "feasibility":
            p = body.profile
            gates = PartialGateInputs(
                est_effort_hours=self.effort,
                available_hours=p.hours_per_week * p.weeks_available * p.team_size,
            )
        return ScorerResponse(scores=scores, gate_inputs=gates)

    async def _validators(self, request: httpx.Request, path: str) -> httpx.Response:
        body = ScorerRequest.model_validate_json(request.content)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.scorer_delay_s)
        finally:
            self.in_flight -= 1
        return _reply(self.scorer_response(path.removeprefix("/score/"), body))

    async def _scouts(self, request: httpx.Request, path: str) -> httpx.Response:
        asked = ScoutRequest.model_validate_json(request.content)
        for card in self.scout_cards:
            self.kb.cards[card.id] = card
        if self.scout_cards:
            self.kb_version = "kb-2"
        return _reply(
            ScoutResponse(
                fetched=min(asked.page_budget, 5),
                ingested_card_ids=[c.id for c in self.scout_cards],
            )
        )

    async def _agentops(self, request: httpx.Request, path: str) -> httpx.Response:
        return httpx.Response(200, json=[r.model_dump(mode="json") for r in self.agent_records])


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now


def settings(**changes: Any) -> OrchestratorSettings:
    values: dict[str, Any] = {
        f"{name}_URL": f"http://{name.lower()}:8000"
        for name in (
            "PROFILE",
            "VERDICT",
            "VALIDATORS",
            "KB",
            "SCOUTS",
            "CRITIC",
            "IDEATION",
            "AGENTOPS",
            "ANALYST",
        )
    }
    del values["AGENTOPS_URL"]
    values |= {
        "agentops_url": None,
        "rubrics_dir": str(ROOT / "rubrics"),
        "prompts_dir": str(ROOT / "prompts"),
        "budgets_path": str(ROOT / "config/budgets.yaml"),
    }
    return OrchestratorSettings.model_validate(values | changes)


class Orchestrator:
    """One orchestrator process on a stack; make a second one for a restart."""

    def __init__(self, stack: Stack, **changes: Any) -> None:
        self.stack = stack

        async def sink(batch: list[TraceRecord]) -> None:
            stack.records.extend(batch)

        self.lines = io.StringIO()  # what would reach stdout
        self.trace = TraceClient(sink=sink, stream=self.lines)
        configured = settings(**changes)
        if configured.agentops_url is None:
            # M3 reads spend from M13; the tracer above stands in for writing to it
            configured = configured.model_copy(update={"agentops_url": "http://agentops:8000"})
        self.app: FastAPI = build_app(
            configured,
            redis=stack.redis,
            transport=stack,
            tracer=self.trace,  # type: ignore[arg-type]
        )
        self.runner = self.app.state.runner
        self.runner.deps.retry_wait_s = 0
        self.runner.deps.clock = Clock()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://orchestrator",
            headers=HEADERS,
        )

    async def wait(self, run_id: str) -> None:
        async with asyncio.timeout(20):
            while (task := self.runner.tasks.get(run_id)) is not None and not task.done():
                await asyncio.wait({task})
        await self.trace.flush()

    async def start(self, version_id: str = "iv_1", **profile_changes: Any) -> str:
        response = await self.client.post(
            "/runs",
            json={
                "workflow": "validate",
                "user_id": USER,
                "idea_version_id": version_id,
                "profile": profile(**profile_changes),
            },
        )
        assert response.status_code == 202, response.text
        return response.json()["run_id"]

    async def discover(self, **changes: Any) -> str:
        """Start a Discover run and wait until it stops."""
        body: dict[str, Any] = {"workflow": "discover", "user_id": USER, "profile": profile()}
        response = await self.client.post("/runs", json=body | changes)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        await self.wait(run_id)
        return run_id

    def children(self, run_id: str) -> list[str]:
        return [r for r, record in self.stack.runs.items() if record.parent_run_id == run_id]

    def verdicts(self, run_id: str) -> list[Verdict]:
        result = self.stack.runs[run_id].result
        assert result is not None, self.state(run_id).get("error_code")
        return [Verdict.model_validate_json(json.dumps(v)) for v in result["verdicts"]]

    def dropped(self, run_id: str) -> list[dict[str, Any]]:
        return [e.data or {} for e in self.of_type(run_id, RunEventType.IDEA_DROPPED)]

    async def run(self, version_id: str = "iv_1", **profile_changes: Any) -> str:
        run_id = await self.start(version_id, **profile_changes)
        await self.wait(run_id)
        return run_id

    async def post(self, run_id: str, action: str, body: dict | None = None) -> httpx.Response:
        response = await self.client.post(f"/runs/{run_id}/{action}", json=body)
        await self.wait(run_id)
        return response

    async def approve(self, run_id: str, decision: str = "approve") -> httpx.Response:
        approval_id = self.state(run_id)["pending"]["approval"]["approval_id"]
        return await self.post(
            run_id, "approve", {"approval_id": approval_id, "decision": decision}
        )

    async def answer(self, run_id: str, text: str = "broad") -> httpx.Response:
        answers = [
            {"question_id": q["question_id"], "answer": text}
            for q in self.state(run_id)["pending"]["questions"]
        ]
        return await self.post(run_id, "answer", {"answers": answers})

    def state(self, run_id: str) -> dict[str, Any]:
        return self.stack.runs[run_id].state

    def status(self, run_id: str) -> str:
        return self.stack.runs[run_id].status.value

    def card(self, run_id: str) -> Verdict:
        result = self.stack.runs[run_id].result
        assert result is not None, self.state(run_id).get("error_code")
        return Verdict.model_validate_json(json.dumps(result["verdicts"][0]))

    def events(self, run_id: str) -> list[RunEvent]:
        return self.stack.redis.events(run_id)

    def of_type(self, run_id: str, kind: RunEventType) -> list[RunEvent]:
        return [e for e in self.events(run_id) if e.type is kind]

    def decisions(self, run_id: str, name: str) -> list[dict[str, Any]]:
        return [
            json.loads(r.output or "{}")
            for r in self.stack.records
            if r.run_id == run_id and r.kind is TraceKind.DECISION and r.tool == name
        ]

    def path(self, run_id: str) -> list[str]:
        steps = self.decisions(run_id, "step")
        return [steps[0]["from"], *(s["to"] for s in steps)] if steps else []
