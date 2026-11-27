import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import respx
from pydantic import BaseModel

from common.contracts.tools import ChatMessage
from common.http import ServiceClient
from common.llm import (
    Cassette,
    LLMCassetteMissError,
    LLMClient,
    LLMOutputError,
    LLMUnavailableError,
)
from common.settings import LlmSettings
from common.trace import TraceClient


class Answer(BaseModel):
    score: int


GOOD = '{"score": 3}'
MESSAGES = [ChatMessage(role="user", content="score it")]


def fake_client(*replies: str, out: io.StringIO | None = None) -> LLMClient:
    return LLMClient(
        LlmSettings(backend="fake"),
        cassette=Cassette([{"content": r, "tokens_in": 10, "tokens_out": 2} for r in replies]),
        tracer=TraceClient(stream=out or io.StringIO()),
    )


async def ask(client: LLMClient, **kwargs: Any) -> Any:
    return await client.chat(
        "agent-default", MESSAGES, Answer, agent="tester", run_id="run_1", **kwargs
    )


async def test_fake_replays_cassette(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(
        json.dumps({"agent": "other", "content": "{}"})
        + "\n"
        + json.dumps({"agent": "tester", "alias": "agent-default", "content": GOOD})
        + "\n"
    )
    settings = LlmSettings(backend="fake", cassette_path=str(path))
    client = LLMClient(settings, tracer=TraceClient(stream=io.StringIO()))
    result = await ask(client)
    assert (result.data, result.repaired) == (Answer(score=3), False)
    with pytest.raises(LLMCassetteMissError):
        await ask(client)


async def test_fenced_json_is_accepted() -> None:
    result = await ask(fake_client(f"```json\n{GOOD}\n```"))
    assert (result.data, result.repaired) == (Answer(score=3), False)


async def test_invalid_json_one_repair_retry() -> None:
    out = io.StringIO()
    result = await ask(fake_client("Sure! three", GOOD, GOOD, out=out), prompt_version="t@v1")
    assert (result.data, result.repaired) == (Answer(score=3), True)
    assert (result.tokens_in, result.tokens_out) == (20, 4)
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["retries"] for r in records] == [0, 1]
    assert {(r["kind"], r["prompt_version"]) for r in records} == {("llm_call", "t@v1")}
    repair_prompt = json.loads(records[1]["input"])
    assert repair_prompt[-2] == {"role": "assistant", "content": "Sure! three"}
    assert "did not match the schema" in repair_prompt[-1]["content"]


async def test_second_invalid_raises_after_exactly_one_retry() -> None:
    client = fake_client("nope", '{"score": "x"}', GOOD)
    with pytest.raises(LLMOutputError):
        await ask(client)
    assert (await ask(client)).data == Answer(score=3)  # the third reply was not consumed


async def test_without_schema_returns_text() -> None:
    result = await fake_client("hello").chat("agent-cheap", MESSAGES, agent="a", run_id="r")
    assert (result.content, result.data) == ("hello", None)


async def test_run_id_required() -> None:
    with pytest.raises(ValueError, match="run_id"):
        await fake_client(GOOD).chat("agent-cheap", MESSAGES, agent="a")


def test_backend_configuration_errors(tmp_path: Path) -> None:
    with pytest.raises(LLMCassetteMissError):
        LLMClient(LlmSettings(backend="fake", cassette_path=None))
    with pytest.raises(LLMCassetteMissError):
        LLMClient(LlmSettings(backend="fake", cassette_path=str(tmp_path / "missing.jsonl")))
    with pytest.raises(ValueError, match="TOOLS_URL"):
        LLMClient(LlmSettings(backend="gateway", tools_url=None))


@respx.mock
async def test_gateway_backend_calls_m5_with_alias() -> None:
    route = respx.post("http://tools:8000/llm/chat").respond(
        200,
        json={
            "content": GOOD, "model": "gemini-x", "tokens_in": 5, "tokens_out": 1,
            "cost_usd": 0.001, "latency_ms": 10, "cache_hit": True,
        },
    )  # fmt: skip
    client = LLMClient(
        LlmSettings(backend="gateway", tools_url="http://tools:8000"),
        tracer=TraceClient(stream=io.StringIO()),
    )
    result = await ask(client)
    await client.aclose()
    sent = json.loads(route.calls.last.request.content)
    assert (sent["model_alias"], sent["run_id"], sent["agent"]) == (
        "agent-default", "run_1", "tester",
    )  # fmt: skip
    assert sent["response_format"]["properties"]["score"]
    assert (result.model, result.cost_usd, result.cache_hit) == ("gemini-x", 0.001, True)


@respx.mock
async def test_gateway_failure_is_unavailable_and_traced() -> None:
    respx.post("http://tools:8000/llm/chat").respond(402)
    out = io.StringIO()
    client = LLMClient(
        LlmSettings(backend="gateway"),
        gateway=ServiceClient("http://tools:8000"),
        tracer=TraceClient(stream=out),
    )
    with pytest.raises(LLMUnavailableError):
        await ask(client)
    assert "LLMUnavailableError" in json.loads(out.getvalue())["error"]


def fake_litellm(calls: list[dict[str, Any]], fail: bool = False) -> SimpleNamespace:
    async def acompletion(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        if fail:
            raise TimeoutError
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )

    def completion_cost(**_: Any) -> float:
        raise ValueError("unknown model price")

    return SimpleNamespace(acompletion=acompletion, completion_cost=completion_cost)


async def test_direct_backend_maps_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm(calls))
    settings = LlmSettings(backend="direct", ollama_base_url="http://ollama:11434")
    client = LLMClient(settings, tracer=TraceClient(stream=io.StringIO()))
    result = await client.chat("local-dev", MESSAGES, Answer, agent="a", run_id="r")
    assert (result.data, result.tokens_in, result.cost_usd) == (Answer(score=3), 7, 0.0)
    assert (calls[0]["model"], calls[0]["api_base"]) == ("ollama/llama3.2", "http://ollama:11434")
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in calls[0]["messages"][0]["content"]
    await client.chat("agent-default", MESSAGES, agent="a", run_id="r")
    assert "api_base" not in calls[1] and "response_format" not in calls[1]


@pytest.mark.parametrize("module", [None, fake_litellm([], fail=True)])
async def test_direct_backend_errors(
    monkeypatch: pytest.MonkeyPatch, module: SimpleNamespace | None
) -> None:
    monkeypatch.setitem(sys.modules, "litellm", module)  # None makes the import fail
    client = LLMClient(LlmSettings(backend="direct"), tracer=TraceClient(stream=io.StringIO()))
    with pytest.raises(LLMUnavailableError):
        await ask(client)
