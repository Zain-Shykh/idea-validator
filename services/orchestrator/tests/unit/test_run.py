import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from common.contracts.core import RunEvent, RunEventType, Verdict, VerdictLabel
from common.contracts.orchestrator import (
    ChallengeResponse,
    StartRunResponse,
    WhatIfApplyResponse,
    WhatIfProposalsResponse,
)
from orchestrator.main import build_app
from orchestrator.services.stub_run import fixture_events, new_id
from orchestrator.settings import OrchestratorSettings
from verdict.main import app as verdict_app


class FakeRedis:
    """The four commands the service uses, in memory."""

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
