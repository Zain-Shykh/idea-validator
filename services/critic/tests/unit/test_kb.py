"""``HttpKnowledgeBase`` and ``/check`` over it, with a mocked M6 (no network)."""

import json
from collections.abc import Callable

import httpx
import pytest
from critic_factories import QUIZ, ROOT, Sink, cards, claim, judge_for, reply, request
from fastapi.testclient import TestClient
from tenacity import wait_none

from common.fakes import FakeKB
from common.http import ServiceError
from common.llm import Cassette
from critic.main import build_app
from critic.services.check import Checker
from critic.services.kb import HttpKnowledgeBase
from critic.settings import CriticSettings

Handler = Callable[[httpx.Request], httpx.Response]
CARD = cards()[0]


def kb(handler: Handler) -> HttpKnowledgeBase:
    built = HttpKnowledgeBase("http://kb:8000", 1, transport=httpx.MockTransport(handler))
    built.client._wait = wait_none()
    return built


async def m6(seen: list[httpx.Request]) -> Handler:
    evidence = (await FakeKB(cards()).evidence([QUIZ])).model_dump_json()

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = evidence if req.url.path == "/evidence" else CARD.model_dump_json()
        return httpx.Response(200, content=body)

    return handle


async def test_evidence_and_card_use_the_m6_api() -> None:
    seen: list[httpx.Request] = []
    client = kb(await m6(seen))
    assert [i.evidence_id for i in (await client.evidence([QUIZ])).items] == [QUIZ]
    found = await client.card(CARD.id)
    assert found is not None and found.name == "QuizForge"
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/evidence"),
        ("GET", f"/cards/{CARD.id}"),
    ]
    assert json.loads(seen[0].content) == {"ids": [QUIZ]}
    await client.aclose()


async def test_a_missing_card_is_none() -> None:
    assert await kb(lambda _: httpx.Response(404)).card(CARD.id) is None


@pytest.mark.parametrize(("status", "attempts"), [(500, 3), (429, 3), (400, 1)])
async def test_other_errors_are_raised_after_bounded_attempts(status: int, attempts: int) -> None:
    seen: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(status)

    with pytest.raises(ServiceError):
        await kb(handle).card(CARD.id)
    assert len(seen) == attempts


def timeout(req: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=req)


def refused(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=req)


@pytest.mark.parametrize(
    "handler",
    [
        timeout,
        refused,
        lambda _: httpx.Response(404),  # /evidence itself is not found
        lambda _: httpx.Response(200, content=b"<html>"),
        lambda _: httpx.Response(200, content=b"[" * 100_000 + b"]" * 100_000),
        lambda _: httpx.Response(200, json={"items": [{"evidence_id": QUIZ}]}),
        lambda _: httpx.Response(200, content=b'{"items": [], "missing": ["\\ud800'),
    ],
    ids=["timeout", "refused", "404", "html", "nested", "bad-item", "truncated"],
)
def test_check_over_a_broken_m6_is_503(handler: Handler) -> None:
    sink = Sink()
    built = Checker(kb(handler), judge_for(Cassette([reply()]), sink.tracer), sink.tracer, 30)
    settings = CriticSettings(
        KB_URL="http://kb:8000",
        prompts_dir=str(ROOT / "prompts"),
        budgets_path=str(ROOT / "config/budgets.yaml"),
    )
    api = TestClient(build_app(settings, checker=built))
    payload = request(claim("it has no offline mode")).model_dump(mode="json")
    response = api.post("/check", json=payload)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "KB_UNAVAILABLE"


async def test_a_404_card_leaves_the_name_to_the_quotes() -> None:
    evidence = (await FakeKB(cards()).evidence([QUIZ])).model_dump_json()
    sink = Sink()
    client = kb(
        lambda r: (
            httpx.Response(200, content=evidence)
            if r.url.path == "/evidence"
            else httpx.Response(404)
        )
    )
    built = Checker(client, judge_for(Cassette([reply()]), sink.tracer), sink.tracer, 30)
    response = await built.check(request(claim("QuizForge has no offline mode")), "run_1")
    assert response.results[0].verdict == "unsupported"
    assert "QuizForge" in response.results[0].reason


async def test_a_card_reply_for_another_id_does_not_vouch_for_a_name() -> None:
    evidence = (await FakeKB(cards()).evidence([QUIZ])).model_dump_json()
    other = cards()[1].model_dump_json()  # PlanPilot, under the quiz card's path
    sink = Sink()
    client = kb(
        lambda r: httpx.Response(200, content=evidence if r.url.path == "/evidence" else other)
    )
    built = Checker(client, judge_for(Cassette([reply()]), sink.tracer), sink.tracer, 30)
    response = await built.check(request(claim("It is weaker than PlanPilot")), "run_1")
    assert response.results[0].verdict == "unsupported"
