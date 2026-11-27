"""AO-7 for M10: recorded model replies, replayed without a model.

``cassettes/scorer_base.jsonl`` holds real replies of the base model (Ollama ``qwen2.5:1.5b``),
recorded with ``python scripts/scorer_check.py --record …``. The invalid and hosted replies are
written by hand.
"""

import asyncio
import json
from pathlib import Path

import httpx
from scorer_factories import ROOT, Replies, Rig, completion, reply, request

from common.contracts.agents import ScoreRequest
from common.contracts.core import DimensionScore, TraceKind
from common.contracts.ops import ScorerFallbackEvent
from common.llm import Cassette
from common.metrics import SCORER_FALLBACKS

CASSETTES = Path(__file__).parent / "cassettes"


def fallbacks() -> float:
    return SCORER_FALLBACKS.labels("demand_pain")._value.get()


def local_replies(name: str) -> list[str]:
    cassette = Cassette.load(CASSETTES / name)
    lines = (CASSETTES / name).read_text().splitlines()
    return [cassette.next("scorer-local", "scorer")["content"] for _ in lines]


def hosted_replies(name: str) -> list[str]:
    cassette = Cassette.load(CASSETTES / name)
    return [cassette.next("scorer-hosted", "scorer")["content"]]


async def test_recorded_base_model_replies_give_valid_scores() -> None:
    lines = (ROOT / "eval/fixtures/score_requests.jsonl").read_text().splitlines()
    requests = [ScoreRequest.model_validate_json(line) for line in lines]
    recorded = [
        json.loads(line) for line in (CASSETTES / "scorer_base.jsonl").read_text().splitlines()
    ]
    assert len(requests) == 10
    assert {r.rubric_id.split("@")[0] for r in requests} == {
        "demand_pain", "competition", "differentiation", "feasibility", "risk", "learning"
    }  # fmt: skip
    for case, body in enumerate(requests):
        server = Replies(*[r["content"] for r in recorded if r["case"] == case])
        result = await Rig(server).scorer.score(body)
        DimensionScore.model_validate_json(result.model_dump_json())
        assert result.model == "scorer-base@v1" and result.score is not None, case
        assert set(result.evidence_ids) <= {e.evidence_id for e in body.evidence}
        assert not server.replies


async def test_invalid_json_retries_once_then_falls_back() -> None:
    server = Replies(*local_replies("scorer_invalid_json.jsonl"))
    rig = Rig(server, hosted_replies("scorer_hosted.jsonl"))
    before = fallbacks()
    result = await rig.scorer.score(request())

    assert len(server.bodies) == 2  # the first call and exactly one retry
    assert (result.model, result.score) == ("scorer-hosted", 3.0)
    assert fallbacks() == before + 1
    records = await rig.records()
    calls = [r for r in records if r.kind is TraceKind.LLM_CALL]
    assert [(c.model, c.retries) for c in calls] == [
        ("scorer-base@v1", 0), ("scorer-base@v1", 1), ("fake", 0)
    ]  # fmt: skip
    (event,) = [r for r in records if r.tool == "scorer_fallback"]
    assert event.model == "scorer-hosted"
    assert ScorerFallbackEvent.model_validate_json(event.output or "").dimension == "demand_pain"


async def test_local_timeout_falls_back() -> None:
    server = Replies(httpx.ReadTimeout("too slow"))
    rig = Rig(server, hosted_replies("scorer_hosted.jsonl"))
    before = fallbacks()
    result = await rig.scorer.score(request())
    assert (result.model, len(server.bodies), fallbacks()) == ("scorer-hosted", 1, before + 1)


async def test_local_deadline_falls_back() -> None:
    async def hangs(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return completion(reply())

    rig = Rig(hangs, hosted_replies("scorer_hosted.jsonl"), local_timeout_s=0.05)
    assert (await rig.scorer.score(request())).model == "scorer-hosted"
    records = await rig.records()
    assert records[0].error and "Timeout" in records[0].error
    assert any(r.tool == "scorer_fallback" and r.model == "scorer-hosted" for r in records)
