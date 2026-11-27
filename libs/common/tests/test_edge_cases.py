"""Edge cases and failure modes (spec A7) and cross-module integration."""

import asyncio
import contextlib
import io
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tenacity import wait_none

import common
from common import context
from common.contracts import export
from common.contracts.agents import ClaimResult, ScorerRequest
from common.contracts.core import (
    DimensionScore,
    EvidenceItem,
    IdeaInput,
    PendingInput,
    Sensitivity,
    StudentProfile,
    TraceKind,
    TraceRecord,
    Verdict,
    VerdictRequest,
)
from common.contracts.kb import SearchResult
from common.contracts.tools import ChatMessage
from common.http import ServiceClient, ServiceError
from common.llm import Cassette, LLMClient, LLMUnavailableError
from common.service import create_app
from common.settings import LlmSettings, ServiceSettings
from common.trace import TraceClient

ROOT = Path(__file__).resolve().parents[3]
MESSAGES = [ChatMessage(role="user", content="hi")]


def verdict_payload(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "idea_id": "idea_1",
        "version": 1,
        "run_id": "run_1",
        "verdict": "PIVOT",
        "stability": "STABLE",
        "score": 60,
        "confidence": 0.7,
        "gates": dict.fromkeys(("evidence", "feasibility", "risk", "duplicate"), "pass"),
        "weights": {
            "demand_pain": 15, "competition": 15, "differentiation": 15,
            "feasibility": 25, "risk": 10, "learning": 20,
        },
        "thresholds": {"T_go": 70, "T_no": 50, "floor": 1.5},
        "scores": request["scores"],
        "weakest_dimension": "risk",
        "what_would_make_go": [
            {"change": "c", "new_score": 71, "new_verdict": "GO", "from": 0.0, "to": 5.0}
        ],
        "sensitivity": {"runs": 2, "flipped": 0},
        "versions": {"engine": "e", "rubrics": "r", "profile_weights": "w"},
    }  # fmt: skip


# --- aliases and strict round-trips ------------------------------------------------------------


async def test_aliased_verdict_survives_client_and_fastapi_both_ways(
    verdict_request: dict[str, Any],
) -> None:
    app = create_app("echo")

    @app.post("/echo")
    async def echo(body: Verdict) -> Verdict:
        return body

    verdict = Verdict.model_validate(verdict_payload(verdict_request))
    transport = httpx.ASGITransport(app=app)
    async with ServiceClient("http://svc", transport=transport) as client:
        raw = await client.post("/echo", verdict)
        typed = await client.post("/echo", verdict, response_model=Verdict)
    change = raw["what_would_make_go"][0]
    assert (change["from"], change["to"]) == (0.0, 5.0)
    assert "from_score" not in change
    assert typed == verdict
    schemas = app.openapi()["components"]["schemas"]
    properties = next(v for k, v in schemas.items() if k.startswith("WhatWouldMakeGo"))
    assert {"from", "to"} <= set(properties["properties"])


@pytest.mark.parametrize("mode", ["python", "json"])
def test_dumped_verdict_revalidates_under_extra_forbid(
    mode: str, verdict_request: dict[str, Any]
) -> None:
    verdict = Verdict.model_validate(verdict_payload(verdict_request))
    assert Verdict.model_validate(verdict.model_dump(mode=mode)) == verdict
    parsed = VerdictRequest.model_validate(verdict_request)
    assert VerdictRequest.model_validate(parsed.model_dump(mode=mode)) == parsed


def test_search_results_passed_as_evidence_drop_search_only_fields(
    profile: dict[str, Any],
) -> None:
    hit = SearchResult(
        evidence_id="card_a#c0", card_id="card_a", quote="q", source="s", url="u",
        retrieved_at="2026-01-01", score=0.9, card_name="A",
    )  # fmt: skip
    request = ScorerRequest(
        run_id="r",
        idea=IdeaInput(title="t", text="x"),
        profile=StudentProfile.model_validate(profile),
        evidence=[hit],
    )
    wire = request.model_dump_json()
    assert type(ScorerRequest.model_validate_json(wire).evidence[0]) is EvidenceItem
    assert "card_name" not in wire


def test_exported_schema_uses_from_and_to() -> None:
    schema = export.render()["core.WhatWouldMakeGo.json"]
    assert {"from", "to"} <= set(json.loads(schema)["properties"])


# --- boundaries --------------------------------------------------------------------------------


def score(**change: Any) -> dict[str, Any]:
    return {
        "dimension": "risk", "score": 3, "confidence": 0.5, "evidence_ids": ["card_a#c0"],
        "reason": "r", "rubric_version": "risk@v1", "model": "m", **change,
    }  # fmt: skip


@pytest.mark.parametrize("change", [{"score": 0}, {"score": 5}, {"confidence": 1}])
def test_score_boundaries_accepted(change: dict[str, Any]) -> None:
    assert DimensionScore.model_validate(score(**change))


@pytest.mark.parametrize(
    "change",
    [
        {"score": -0.01},
        {"score": float("inf")},
        {"confidence": -0.1},
        {"reason": "   "},
        {"evidence_ids": ["card_a#c0 "]},
        {"evidence_ids": ["card_a#cx"]},
    ],
)
def test_score_out_of_bounds_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DimensionScore.model_validate(score(**change))


def test_idea_input_length_limits() -> None:
    IdeaInput(title="t" * 200, text="x" * 5000)
    for title, text in [("t" * 201, "x"), ("t", "x" * 5001), (" ", "x")]:
        with pytest.raises(ValidationError):
            IdeaInput(title=title, text=text)


def test_cross_field_rules() -> None:
    with pytest.raises(ValidationError):
        Sensitivity(runs=1, flipped=2)
    with pytest.raises(ValidationError):
        PendingInput(kind="question")
    with pytest.raises(ValidationError):
        ClaimResult(claim_id="c", supported=True, verdict="unverifiable", reason="r")


# --- bad configuration -------------------------------------------------------------------------


def test_bad_backend_and_log_level_fail_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BACKEND", "openai")
    with pytest.raises(ValidationError):
        LlmSettings()
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValueError):
        create_app("demo")
    monkeypatch.delenv("LOG_LEVEL")
    create_app("demo")  # restore root logging


def test_empty_agentops_url_means_stdout_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOPS_URL", "")
    assert TraceClient(ServiceSettings().agentops_url)._sink is None


def test_package_has_no_localhost() -> None:
    source = Path(common.__file__).parent
    assert not [p for p in source.rglob("*.py") if "localhost" in p.read_text()]


@pytest.mark.parametrize("pattern", [".env", "*.env", "*.pem", "*.key"])
def test_dockerignore_excludes_secrets_at_any_depth(pattern: str) -> None:
    # Docker matches a bare pattern at the context root only; the context is the repo root
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    assert f"**/{pattern}" in lines


# --- app: concurrency, lifespan, streaming -----------------------------------------------------


async def test_concurrent_requests_do_not_share_context() -> None:
    app = create_app("ctx")

    @app.get("/ids")
    async def ids(delay: float) -> dict[str, str]:
        await asyncio.sleep(delay)
        return context.outgoing_headers()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as client:
        replies = await asyncio.gather(
            *(
                client.get(
                    "/ids",
                    params={"delay": (5 - i) / 200},
                    headers={"X-Run-Id": f"run_{i}", "X-User-Id": f"u{i}"},
                )
                for i in range(5)
            )
        )
    assert [(r.json()["X-Run-Id"], r.json()["X-User-Id"]) for r in replies] == [
        (f"run_{i}", f"u{i}") for i in range(5)
    ]
    assert context.outgoing_headers() == {}


def test_streaming_response_keeps_context_and_headers() -> None:
    app = create_app("stream")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def body() -> AsyncIterator[str]:
            for _ in range(3):
                await asyncio.sleep(0)
                yield f"{context.run_id_var.get()};"

        return StreamingResponse(body(), media_type="text/event-stream")

    response = TestClient(app).get("/stream", headers={"X-Run-Id": "run_s"})
    assert response.text == "run_s;" * 3
    assert response.headers["X-Run-Id"] == "run_s"


def test_create_app_with_lifespan_flushes_traces() -> None:
    sent: list[TraceRecord] = []

    async def sink(batch: list[TraceRecord]) -> None:
        sent.extend(batch)

    tracer = TraceClient(sink=sink, stream=io.StringIO())

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(tracer.run_periodic_flush(60))
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await tracer.aclose()

    app = create_app("life", lifespan=lifespan)

    @app.get("/work")
    async def work() -> None:
        with tracer.step("a", TraceKind.DECISION):
            pass

    with TestClient(app) as client:
        client.get("/work", headers={"X-Run-Id": "run_l", "X-Parent-Step": "9"})
    assert [(r.run_id, r.parent_step) for r in sent] == [("run_l", 9)]


# --- TraceClient -------------------------------------------------------------------------------


def emit(tracer: TraceClient, ids: range) -> None:
    for index in ids:
        with tracer.step("a", TraceKind.DECISION, run_id="r", step_id=index):
            pass


async def test_concurrent_flushes_and_emits_send_each_record_once_in_order() -> None:
    sent: list[int] = []

    async def sink(batch: list[TraceRecord]) -> None:
        await asyncio.sleep(0.001)
        sent.extend(r.step_id for r in batch)

    tracer = TraceClient(sink=sink, batch_size=3, stream=io.StringIO())
    emit(tracer, range(7))
    first = asyncio.create_task(tracer.flush())
    await asyncio.sleep(0)
    emit(tracer, range(7, 15))
    await asyncio.gather(first, tracer.flush(), tracer.flush())
    await tracer.aclose()
    assert sent == list(range(15))


async def test_requeue_with_buffer_filled_during_send_drops_oldest() -> None:
    sent: list[int] = []
    calls = 0
    tracer: TraceClient

    async def sink(batch: list[TraceRecord]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            emit(tracer, range(3, 5))
            raise ConnectionError
        sent.extend(r.step_id for r in batch)

    tracer = TraceClient(sink=sink, batch_size=3, max_buffer=3, stream=io.StringIO())
    emit(tracer, range(3))
    await tracer.aclose()  # first (automatic or explicit) send fails
    await tracer.flush()
    assert sent == [2, 3, 4]


async def test_periodic_flush_delivers() -> None:
    delivered = asyncio.Event()

    async def sink(_: list[TraceRecord]) -> None:
        delivered.set()

    tracer = TraceClient(sink=sink, stream=io.StringIO())
    emit(tracer, range(1))
    task = asyncio.create_task(tracer.run_periodic_flush(0))
    await asyncio.wait_for(delivered.wait(), 1)
    task.cancel()


async def test_cancelling_flush_mid_send_keeps_records() -> None:
    """The documented shutdown is 'cancel run_periodic_flush, then aclose()'."""
    sent: list[int] = []
    sending = asyncio.Event()

    async def sink(batch: list[TraceRecord]) -> None:
        if not sending.is_set():
            sending.set()
            await asyncio.sleep(10)
        sent.extend(r.step_id for r in batch)

    tracer = TraceClient(sink=sink, stream=io.StringIO())
    emit(tracer, range(3))
    task = asyncio.create_task(tracer.run_periodic_flush(0))
    await asyncio.wait_for(sending.wait(), 1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await tracer.aclose()
    assert sent == [0, 1, 2]


def test_emit_without_event_loop_and_with_closed_stream_never_raises() -> None:
    sent: list[TraceRecord] = []

    async def sink(batch: list[TraceRecord]) -> None:
        sent.extend(batch)

    stream = io.StringIO()
    stream.close()
    tracer = TraceClient(sink=sink, batch_size=1, stream=stream)
    emit(tracer, range(2))
    asyncio.run(tracer.aclose())
    assert len(sent) == 2


async def test_parallel_steps_do_not_leak_parent() -> None:
    out = io.StringIO()
    tracer = TraceClient(stream=out)

    async def child(index: int) -> None:
        with tracer.step("c", TraceKind.DECISION, run_id="r", step_id=index):
            await asyncio.sleep(0.001)
            assert context.parent_step_var.get() == index

    with tracer.step("p", TraceKind.DECISION, run_id="r", step_id=100):
        await asyncio.gather(child(1), child(2), child(3))
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert {r["step_id"]: r["parent_step"] for r in records} == {
        1: 100, 2: 100, 3: 100, 100: None,
    }  # fmt: skip


# --- LLM and HTTP failures ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout("slow"), httpx.Response(200, json={"content": "x"})]
)
@respx.mock
async def test_gateway_timeout_or_bad_shape_is_unavailable(failure: object) -> None:
    route = respx.post("http://tools/llm/chat").mock(side_effect=[failure] * 3)
    out = io.StringIO()
    client = LLMClient(
        LlmSettings(backend="gateway"),
        gateway=ServiceClient("http://tools", wait=wait_none()),
        tracer=TraceClient(stream=out),
    )
    with pytest.raises(LLMUnavailableError):
        await client.chat("agent-cheap", MESSAGES, agent="a", run_id="r")
    assert route.call_count == (3 if isinstance(failure, Exception) else 1)
    assert json.loads(out.getvalue())["error"]


async def test_direct_backend_empty_choices_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def acompletion(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(choices=[], usage=None)

    fake = SimpleNamespace(acompletion=acompletion, completion_cost=lambda **_: 0)
    monkeypatch.setitem(sys.modules, "litellm", fake)
    client = LLMClient(LlmSettings(backend="direct"), tracer=TraceClient(stream=io.StringIO()))
    with pytest.raises(LLMUnavailableError):
        await client.chat("local-dev", MESSAGES, agent="a", run_id="r")


async def test_exhausted_cassette_is_traced() -> None:
    out = io.StringIO()
    client = LLMClient(
        LlmSettings(backend="fake"), cassette=Cassette([]), tracer=TraceClient(stream=out)
    )
    with pytest.raises(common.llm.LLMCassetteMissError):
        await client.chat("agent-cheap", MESSAGES, agent="a", run_id="r")
    assert "LLMCassetteMissError" in json.loads(out.getvalue())["error"]


@pytest.mark.parametrize("body", [{"error": "plain string"}, [1], {"error": {"code": "X"}}])
@respx.mock
async def test_malformed_error_bodies_become_http_error(body: object) -> None:
    respx.get("http://svc/x").respond(400, json=body)
    async with ServiceClient("http://svc") as client:
        with pytest.raises(ServiceError) as caught:
            await client.get("/x")
    assert (caught.value.code, caught.value.status) == ("HTTP_ERROR", 400)


@pytest.mark.parametrize(("status", "kept"), [(422, False), (400, False), (503, True), (404, True)])
async def test_rejected_batch_does_not_block_later_records(status: int, kept: bool) -> None:
    sent: list[int] = []
    calls = 0

    async def sink(batch: list[TraceRecord]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ServiceError("X", "x", status)
        sent.extend(r.step_id for r in batch)

    tracer = TraceClient(sink=sink, batch_size=10, stream=io.StringIO())
    emit(tracer, range(2))
    await tracer.flush()
    emit(tracer, range(2, 3))
    await tracer.flush()
    assert sent == ([0, 1, 2] if kept else [2])


@pytest.mark.parametrize("line", ['{"agent": "a"}', "[1]", '{"content": 3}', "not json"])
def test_malformed_cassette_line_is_a_cassette_error(tmp_path: Path, line: str) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(line + "\n")
    with pytest.raises(common.llm.LLMCassetteMissError):
        Cassette.load(path)
