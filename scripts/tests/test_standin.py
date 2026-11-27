"""The stand-in for M2, M5 and M6 answers in the shared contracts, so that a failure on the
cluster is a fault of the deployed services and not of the stand-in."""

from pathlib import Path

import httpx
import pytest
import yaml

from common.contracts.agents import ConcretizeResponse, IntakeResponse
from common.contracts.kb import KbStats, SearchResponse
from common.contracts.profile import Idea, RunRecord
from common.contracts.tools import LlmChatResponse
from critic.services.judge import JudgeReply
from ideation.services.agents import ObjectionDrafts
from standin import MODEL, REPLIES, build_app
from validators.services.effort import EffortReply

FIXTURES = Path(__file__).resolve().parents[2] / "eval/fixtures"
# the two files run.sh mounts
CARDS = (FIXTURES / "cards_small.jsonl", FIXTURES / "validator_cards.jsonl")


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(*CARDS)), base_url="http://standin"
    )


def test_every_reply_matches_the_schema_the_agent_asks_for() -> None:
    schemas = {
        "intake": IntakeResponse,
        "validators": EffortReply,
        "critic": JudgeReply,
        "skeptic": ObjectionDrafts,
    }
    for agent, schema in schemas.items():
        schema.model_validate(REPLIES[agent])
    assert REPLIES["concretizer"] == {"changes": []}
    assert ConcretizeResponse(changes=[]).changes == []
    assert set(REPLIES) == {*schemas, "concretizer"}


def chat(agent: str, alias: str = "agent-cheap") -> dict:
    return {
        "run_id": "r1",
        "agent": agent,
        "model_alias": alias,
        "messages": [{"role": "user", "content": "x"}],
    }


async def test_it_says_what_it_is_and_never_scores(client: httpx.AsyncClient) -> None:
    assert (await client.get("/stand-in")).json() == {"stand_in": True}
    reply = await client.post("/llm/chat", json=chat("validators"))
    assert LlmChatResponse.model_validate_json(reply.text).model == MODEL
    assert (await client.post("/llm/chat", json=chat("scorer", "scorer-hosted"))).status_code == 503
    # even with a known agent name, the scorer's alias is refused
    assert (await client.post("/llm/chat", json=chat("critic", "scorer-hosted"))).status_code == 503
    assert (await client.post("/llm/chat", json=chat("ideator"))).status_code == 503


async def test_ideas_runs_and_cache(client: httpx.AsyncClient) -> None:
    body = {"user_id": "u", "title": "t", "text": "an idea", "source": "own"}
    first = await client.post("/internal/ideas", json=body, headers={"Idempotency-Key": "k"})
    again = await client.post("/internal/ideas", json=body, headers={"Idempotency-Key": "k"})
    idea = Idea.model_validate_json(first.text)
    assert again.json()["id"] == idea.id
    version = idea.versions[0].id
    assert (await client.get(f"/internal/ideas/versions/{version}")).json()["id"] == idea.id
    run = {
        "run_id": "run_1",
        "user_id": "u",
        "workflow": "validate",
        "profile_version": 1,
        "budget": {"max_tokens": 1, "max_cost_usd": 1, "max_pages": 1, "max_steps": 1},
    }
    assert (await client.post("/internal/runs", json=run)).status_code == 200
    patched = await client.patch("/internal/runs/run_1", json={"status": "RUNNING"})
    assert RunRecord.model_validate_json(patched.text).status.value == "RUNNING"
    assert (await client.get("/internal/runs/nope")).status_code == 404
    missing = await client.get("/internal/cache/verdicts/a/b")
    assert missing.json() == {"hit": False, "verdict": None}


async def test_knowledge_base(client: httpx.AsyncClient) -> None:
    stats = KbStats.model_validate_json((await client.get("/stats")).text)
    assert stats.kb_version == "stand-in" and sum(stats.cards_by_source.values()) > 0
    reply = await client.post("/search", json={"query": "flashcards lecture slides", "k": 5})
    found = SearchResponse.model_validate_json(reply.text)
    assert found.results and found.index_version == "stand-in"
    assert (await client.get(f"/cards/{found.results[0].card_id}")).status_code == 200
    assert (await client.get("/cards/card_ffffffff")).status_code == 404
    asked = {"ids": [found.results[0].evidence_id]}
    assert (await client.post("/evidence", json=asked)).json()["missing"] == []


def test_every_object_of_the_manifest_parses_and_says_it_is_a_stand_in() -> None:
    text = (Path(__file__).resolve().parents[1] / "standins/standin.yaml").read_text()
    objects = list(yaml.safe_load_all(text))
    assert sorted(o["metadata"]["name"] for o in objects) == ["kb", "profile", "standin", "tools"]
    # run.sh removes by this label
    assert all(o["metadata"]["labels"]["idea-validator/stand-in"] == "true" for o in objects)
