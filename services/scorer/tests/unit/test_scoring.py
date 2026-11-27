"""The §14.2 flow. Invalid-JSON and timeout cassettes live in eval/behaviour (AO-7)."""

import asyncio

import httpx
import pytest
from scorer_factories import IDS, Replies, Rig, completion, reply, request

from common.contracts.core import TraceKind
from common.llm import LLMClient
from common.settings import LlmSettings
from scorer.services.scoring import UnknownRubricError


async def test_valid_local_reply_is_the_score() -> None:
    rig = Rig(Replies(reply()))
    result = await rig.scorer.score(request())
    assert (result.score, result.confidence, result.model) == (3.5, 0.7, "scorer-base@v1")
    assert result.evidence_ids == IDS and result.features == {"n_sources": 2}
    assert result.rubric_version == "demand_pain@v1"
    llm, decision = await rig.records()
    assert (llm.kind, llm.tokens_in, llm.tokens_out) == (TraceKind.LLM_CALL, 500, 80)
    assert (llm.model, llm.prompt_version, llm.retries) == ("scorer-base@v1", "scorer@v1", 0)
    assert llm.parent_step == decision.step_id and decision.tool == "score"
    assert llm.run_id == decision.run_id and llm.run_id.startswith("run_adhoc_")


async def test_fenced_reply_needs_no_retry() -> None:
    server = Replies(f"```json\n{reply()}\n```")
    assert (await Rig(server).scorer.score(request())).score == 3.5
    assert len(server.bodies) == 1


@pytest.mark.parametrize(
    "bad",
    [
        reply(score=3.3),
        reply(score=6),
        reply(confidence=1.5),
        reply(reason="  "),
        reply(verdict="GO"),
        '{"score": 3}',
        "",
    ],
)
async def test_invalid_reply_gets_one_repair_retry(bad: str) -> None:
    server = Replies(bad, reply(score=4))
    result = await Rig(server).scorer.score(request())
    assert (result.score, result.model) == (4, "scorer-base@v1")
    first, second = server.bodies
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "user"]
    assert second["messages"][:2] == first["messages"]
    assert "did not match the schema" in second["messages"][-1]["content"]


async def test_unknown_ids_are_stripped() -> None:
    cited = ["[card_a1#c0]", "card_zz#c9", "card_a1#c0", "OpenQuiz"]
    text = "See card_a1#c0, card_zz#c9 and card_zz; card card_a2 agrees."
    result = await Rig(Replies(reply(cited, reason=text))).scorer.score(request())
    assert result.evidence_ids == ["card_a1#c0"] and result.score == 3.5
    assert result.reason == "See card_a1#c0, [unknown id] and [unknown id]; card card_a2 agrees."


@pytest.mark.parametrize("cited", [[], ["card_zz#c9"], ["card_a1#c1", "1"]])
async def test_only_unknown_ids_abstains(cited: list[str]) -> None:
    server = Replies(reply(cited))
    result = await Rig(server).scorer.score(request())
    assert (result.score, result.confidence, result.evidence_ids) == (None, 0, [])
    assert result.model == "scorer-base@v1" and len(server.bodies) == 1


async def test_ids_left_out_of_the_prompt_are_not_accepted() -> None:
    ids = [f"card_c{i}#c0" for i in range(14)]
    result = await Rig(Replies(reply(["card_c13#c0"]))).scorer.score(request(ids))
    assert result.score is None


async def test_no_evidence_abstains_without_a_model_call() -> None:
    server = Replies()
    result = await Rig(server).scorer.score(request([]))
    assert (result.score, result.model, result.reason) == (None, "none", "no evidence")
    assert not server.bodies


@pytest.mark.parametrize("rubric_id", ["vibes@v1", "demand_pain@v2", "demand_pain@v01"])
async def test_unknown_rubric_or_version_is_an_error(rubric_id: str) -> None:
    with pytest.raises(UnknownRubricError):
        await Rig(Replies()).scorer.score(request(rubric_id=rubric_id))


@pytest.mark.parametrize("hosted", [[], ["not json", "still not json"]])
async def test_abstains_when_both_models_fail(hosted: list[str]) -> None:
    rig = Rig(Replies(httpx.ConnectError("refused")), hosted)
    result = await rig.scorer.score(request())
    assert (result.score, result.confidence, result.model) == (None, 0, "none")
    assert result.reason.startswith("scoring failed: local model")


async def test_hosted_gateway_down_abstains() -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no M5")

    from common.http import ServiceClient

    gateway = ServiceClient("http://tools", retries=0, transport=httpx.MockTransport(down))
    llm = LLMClient(LlmSettings(LLM_BACKEND="gateway"), gateway=gateway)
    rig = Rig(Replies(httpx.ConnectError("refused")), hosted_client=llm)
    assert (await rig.scorer.score(request())).model == "none"


async def test_repair_retry_shares_the_local_deadline() -> None:
    calls = []

    async def slow(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        await asyncio.sleep(0.6)
        return completion("not json")

    rig = Rig(slow, [reply()], local_timeout_s=1.0)
    assert (await rig.scorer.score(request())).model == "scorer-hosted"
    assert len(calls) == 2


async def test_hosted_backend_is_not_a_fallback() -> None:
    rig = Rig(None, [reply()])
    result = await rig.scorer.score(request())
    assert (result.score, result.model) == (3.5, "scorer-hosted")
    assert not [r for r in await rig.records() if r.tool == "scorer_fallback"]
