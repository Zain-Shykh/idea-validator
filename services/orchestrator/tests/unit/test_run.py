import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from agentops.main import app as agentops_app
from common.contracts.core import RunEvent, RunEventType, TraceRecord, Verdict, VerdictLabel
from common.contracts.orchestrator import (
    ChallengeResponse,
    StartRunResponse,
    WhatIfApplyResponse,
    WhatIfProposalsResponse,
)
from common.http import ServiceClient
from common.trace import TraceClient
from orchestrator.main import build_app
from orchestrator.services.stub_run import fixture_events, new_id
from orchestrator.settings import OrchestratorSettings
from verdict.main import app as verdict_app


class FakePipeline:
    """Queues commands and runs them on ``execute``, like the real transaction."""

    def __init__(self, redis: "FakeRedis") -> None:
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


class FakeRedis:
    """The commands the service uses, in memory."""

    def __init__(self) -> None:
        self.streams: dict[str, list[dict[str, str]]] = {}
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

    async def expire(self, key: str, seconds: int) -> None:
        self._check()
        self.ttl[key] = seconds

    async def exists(self, key: str) -> int:
        self._check()
        return int(key in self.streams)

    async def aclose(self) -> None:
        pass

    def events(self, run_id: str) -> list[RunEvent]:
        return [
            RunEvent.model_validate_json(e["event"]) for e in self.streams[f"run:{run_id}:events"]
        ]


PROFILE = {
    "user_id": "u_1",
    "skills": ["python"],
    "hours_per_week": 10,
    "weeks_available": 12,
    "team_size": 2,
    "compute": "laptop",
    "goal": "startup",
    "interests": [],
}
START = {
    "workflow": "validate",
    "user_id": "u_1",
    "idea_version_id": "idea_7",
    "profile": PROFILE,
}


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


def client_for(
    redis: FakeRedis,
    transport: httpx.AsyncBaseTransport | None = None,
    verdict_url: str | None = "http://verdict:8000",
) -> TestClient:
    settings = OrchestratorSettings.model_validate({"VERDICT_URL": verdict_url})
    transport = transport or httpx.ASGITransport(app=verdict_app)
    return TestClient(build_app(settings, redis=redis, verdict_transport=transport))  # type: ignore[arg-type]


def start(client: TestClient) -> str:
    response = client.post("/runs", json=START)
    assert response.status_code == 202
    return StartRunResponse.model_validate_json(response.text).run_id


def test_run_emits_fixture_sequence(redis: FakeRedis) -> None:
    """The verdict is fetched from the real verdict stub app over ASGI."""
    run_id = start(client_for(redis))
    events = redis.events(run_id)
    expected = fixture_events()
    assert [(e.seq, e.type, e.agent, e.step) for e in events] == [
        (e.seq, e.type, e.agent, e.step) for e in expected
    ]
    assert {e.run_id for e in events} == {run_id}
    assert redis.ttl[f"run:{run_id}:events"] == 86400
    (card,) = [e for e in events if e.type is RunEventType.VERDICT]
    assert card.data is not None
    verdict = Verdict.model_validate_json(json.dumps(card.data["verdict"]))
    assert verdict.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE
    assert (verdict.run_id, verdict.idea_id) == (run_id, "idea_7")
    assert card.message == "INSUFFICIENT_EVIDENCE"
    assert all(s.score is None for s in verdict.scores)


@pytest.mark.parametrize(
    "reply", [httpx.Response(400, json={}), httpx.Response(200, json={"verdict": "GO"})]
)
def test_bad_verdict_reply_fails_the_run(redis: FakeRedis, reply: httpx.Response) -> None:
    client = client_for(redis, httpx.MockTransport(lambda _: reply))
    events = redis.events(start(client))
    assert [e.type for e in events[-2:]] == [RunEventType.ERROR, RunEventType.RUN_FINISHED]
    assert RunEventType.VERDICT not in {e.type for e in events}
    assert [e.seq for e in events] == list(range(len(events)))


def test_redis_down_gives_503_and_no_run(redis: FakeRedis) -> None:
    redis.down = True
    response = client_for(redis).post("/runs", json=START)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(redis: FakeRedis, path: str) -> None:
    assert client_for(redis).get(path).status_code == 200


def test_ready_fails_without_redis(redis: FakeRedis) -> None:
    redis.down = True
    client = client_for(redis)
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200


def test_ready_fails_without_verdict_url(redis: FakeRedis) -> None:
    assert client_for(redis, verdict_url=None).get("/ready").status_code == 503


def test_answer_approve_resume(redis: FakeRedis) -> None:
    client = client_for(redis)
    run_id = start(client)
    bodies: dict[str, dict[str, Any] | None] = {
        "answer": {"answers": [{"question_id": "q1", "answer": "yes"}]},
        "approve": {"approval_id": "a1", "decision": "decline"},
        "resume": None,
    }
    for action, body in bodies.items():
        assert client.post(f"/runs/{run_id}/{action}", json=body).status_code == 202
        assert client.post(f"/runs/run_missing/{action}", json=body).status_code == 404


def test_challenge_returns_verdict_from_m11(redis: FakeRedis) -> None:
    response = client_for(redis).post(
        "/ideas/idea_7/challenge", json={"version": 3, "claim_id": "c1"}
    )
    result = ChallengeResponse.model_validate_json(response.text)
    assert (result.outcome, result.verdict.idea_id, result.verdict.version) == (
        "removed",
        "idea_7",
        3,
    )


def test_challenge_503_when_verdict_is_down(redis: FakeRedis) -> None:
    client = client_for(redis, httpx.MockTransport(lambda _: httpx.Response(400, json={})))
    response = client.post("/ideas/idea_7/challenge", json={"version": 1, "claim_id": "c1"})
    assert response.status_code == 503


def test_whatif_proposals_then_apply(redis: FakeRedis) -> None:
    client = client_for(redis)
    listed = client.post("/ideas/idea_7/whatif", json={"version": 1})
    assert listed.status_code == 200
    assert WhatIfProposalsResponse.model_validate_json(listed.text).proposals == []
    applied = client.post("/ideas/idea_7/whatif", json={"version": 1, "apply": "p1"})
    assert applied.status_code == 202
    result = WhatIfApplyResponse.model_validate_json(applied.text)
    assert result.new_version == 2
    assert redis.events(result.run_id)[-1].type is RunEventType.RUN_FINISHED


def test_packaged_fixture_matches_shared_fixture() -> None:
    root = Path(__file__).resolve().parents[4]
    shared = root / "eval/fixtures/run_events_validate.jsonl"
    packaged = root / "services/orchestrator/src/orchestrator/fixtures/run_events_validate.jsonl"
    assert packaged.read_bytes() == shared.read_bytes()


def test_new_id_is_a_ulid() -> None:
    first, second = new_id("run"), new_id("run")
    assert first != second
    assert len(first) == len("run_") + 26 and first.startswith("run_0")


# --- spec A7: failure modes -------------------------------------------------------------------


def stored_runs(redis: FakeRedis) -> list[list[RunEvent]]:
    return [redis.events(key.split(":")[1]) for key in redis.streams]


@pytest.mark.parametrize(
    ("failure", "code", "attempts"),
    [
        (httpx.ReadTimeout("slow"), "TIMEOUT", 3),
        (httpx.ConnectError("refused"), "UNAVAILABLE", 1),
        (httpx.Response(500), "HTTP_ERROR", 3),
        (httpx.Response(200, content=b"<html>"), "INVALID_RESPONSE", 1),
        (httpx.Response(200), "INVALID_RESPONSE", 1),
    ],
)
def test_verdict_failure_ends_the_run_after_bounded_attempts(
    redis: FakeRedis, failure: Exception | httpx.Response, code: str, attempts: int
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if isinstance(failure, Exception):
            raise failure
        return failure

    events = redis.events(start(client_for(redis, httpx.MockTransport(handler))))
    error, finished = events[-2:]
    assert (error.type, error.data) == (RunEventType.ERROR, {"code": code})
    assert (finished.type, finished.message) == (RunEventType.RUN_FINISHED, "Run failed")
    assert RunEventType.VERDICT not in {e.type for e in events}
    assert [e.seq for e in events] == list(range(len(events)))
    assert len(calls) == attempts  # AVOID-1: retries <= 2


class FlakyRedis(FakeRedis):
    """Goes down after ``writes`` successful appends."""

    def __init__(self, writes: int) -> None:
        super().__init__()
        self.writes = writes

    async def xadd(self, key: str, fields: dict[str, str], **kwargs: Any) -> None:
        if self.writes == 0:
            self.down = True
        self.writes -= 1
        await super().xadd(key, fields, **kwargs)


@pytest.mark.parametrize("writes", [1, 5, len(fixture_events()) - 2])
def test_redis_failing_mid_run_is_logged_and_does_not_crash(
    writes: int, caplog: pytest.LogCaptureFixture
) -> None:
    redis = FlakyRedis(writes)
    client = client_for(redis)
    with caplog.at_level("ERROR", logger="orchestrator.stub_run"):
        start(client)  # an exception in the background task would propagate here
    assert "could not write run events" in [r.getMessage() for r in caplog.records]
    assert client.get("/health").status_code == 200


def test_redis_failing_while_reporting_a_verdict_failure_does_not_crash() -> None:
    redis = FlakyRedis(len(fixture_events()) - 2)  # down just before the error event
    start(client_for(redis, httpx.MockTransport(lambda _: httpx.Response(400))))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/runs/run_1/resume", None),
        ("/runs/run_1/approve", {"approval_id": "a", "decision": "approve"}),
        ("/ideas/idea_1/whatif", {"version": 1, "apply": "p1"}),
    ],
)
def test_other_redis_routes_give_503_when_redis_is_down(
    redis: FakeRedis, path: str, body: dict[str, Any] | None
) -> None:
    redis.down = True
    response = client_for(redis).post(path, json=body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


@pytest.mark.parametrize("run_id", ["*", "run_%3F", "x" * 2000, "%00", "a:events", "%2e%2e%2fz"])
def test_unusual_run_ids_are_unknown(redis: FakeRedis, run_id: str) -> None:
    client = client_for(redis)
    start(client)
    response = client.post(f"/runs/{run_id}/resume")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# A blank id passes request validation but not VerdictRequest (idea_id is NonEmptyStr).


def test_blank_idea_version_id_is_rejected_before_a_run_starts(redis: FakeRedis) -> None:
    client = client_for(redis)
    response = client.post("/runs", json={**START, "idea_version_id": "   "})
    assert response.status_code == 422
    assert redis.streams == {}


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("challenge", {"version": 1, "claim_id": "c1"}),
        ("whatif", {"version": 1, "apply": "p1"}),
    ],
)
def test_blank_idea_id_in_path_is_not_a_server_error(
    redis: FakeRedis, action: str, body: dict[str, Any]
) -> None:
    response = client_for(redis).post(f"/ideas/%20/{action}", json=body)
    assert response.status_code in {404, 422}
    assert redis.streams == {}


async def test_unresponsive_redis_answers_503_within_seconds() -> None:
    """A Redis that accepts connections and never replies, through the real client."""
    hang = asyncio.Event()

    async def silent(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await hang.wait()
        writer.close()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    settings = OrchestratorSettings.model_validate(
        {
            "VERDICT_URL": "http://verdict:8000",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": server.sockets[0].getsockname()[1],
        }
    )
    transport = httpx.ASGITransport(app=build_app(settings))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # one 3 s socket timeout plus margin
            async with asyncio.timeout(10):
                response = await client.post("/runs", json=START)
        assert response.status_code == 503
    finally:
        hang.set()
        server.close()


# --- concurrency, tracing, lifespan, environment ----------------------------------------------


async def test_concurrent_runs_do_not_mix_events_or_correlation_ids(redis: FakeRedis) -> None:
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.headers["X-Run-Id"], body["run_id"]))
        await asyncio.sleep(0.01)  # let the other runs interleave
        reply = await httpx.ASGITransport(app=verdict_app).handle_async_request(request)
        return httpx.Response(reply.status_code, content=await reply.aread())

    settings = OrchestratorSettings.model_validate({"VERDICT_URL": "http://verdict:8000"})
    app = build_app(settings, redis=redis, verdict_transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        replies = await asyncio.gather(
            *(client.post("/runs", json={**START, "idea_version_id": f"i{n}"}) for n in range(20))
        )
    run_ids = [r.json()["run_id"] for r in replies]
    assert len(set(run_ids)) == 20
    assert len(seen) == 20 and all(header == body for header, body in seen)
    for number, run_id in enumerate(run_ids):
        events = redis.events(run_id)
        assert [e.seq for e in events] == list(range(len(fixture_events())))
        assert {e.run_id for e in events} == {run_id}
        (card,) = [e for e in events if e.type is RunEventType.VERDICT]
        assert card.data is not None and card.data["verdict"]["idea_id"] == f"i{number}"


def test_one_decision_trace_is_flushed_to_agentops_at_shutdown(
    redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec A5. The batch is also checked against the real AgentOps stub."""
    delivered: list[TraceRecord] = []

    async def sink(batch: list[TraceRecord]) -> None:
        async with ServiceClient(
            "http://agentops", transport=httpx.ASGITransport(app=agentops_app)
        ) as agentops:
            await agentops.post("/trace", batch)  # raises unless accepted
        delivered.extend(batch)

    monkeypatch.setattr("orchestrator.main.TraceClient", lambda _: TraceClient(sink=sink))
    with client_for(redis) as client:
        run_id = start(client)
    (record,) = delivered
    assert (record.run_id, record.agent, record.kind) == (run_id, "orchestrator", "decision")
    assert record.output == "verdict=INSUFFICIENT_EVIDENCE" and record.error is None


def test_agentops_down_does_not_affect_the_run_or_shutdown(
    redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTOPS_URL", "http://127.0.0.1:1")
    with client_for(redis) as client:  # exiting runs the lifespan shutdown and final flush
        events = redis.events(start(client))
    assert events[-1].type is RunEventType.RUN_FINISHED
    assert RunEventType.ERROR not in {e.type for e in events}


def test_empty_environment_values_count_as_unset(
    redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("VERDICT_URL", "AGENTOPS_URL", "REDIS_PASSWORD"):
        monkeypatch.setenv(name, "")
    with TestClient(build_app(redis=redis)) as client:  # type: ignore[arg-type]
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        events = redis.events(start(client))
    assert (events[-2].type, events[-2].data) == (RunEventType.ERROR, {"code": "UNAVAILABLE"})
    assert events[-1].type is RunEventType.RUN_FINISHED
