"""Discover and challenge with the real M8, M9, M11, M12 and M13 apps, in process (spec Z8, A4
#6, #11, #14). Model replies are cassettes; M2, M6 and M7 are fakes. No real model is called.
"""

from pathlib import Path
from typing import Any

import httpx
from behaviour_stack import RealStack, all_records, orchestrator
from critic_factories import reply
from orchestrator_factories import GAP_EVIDENCE, Orchestrator

from common.contracts.core import Dimension, GateResult, TraceKind, VerdictLabel
from common.fakes import FakeKB
from common.llm import Cassette, LLMClient
from common.registry import load_prompts
from common.settings import LlmSettings
from ideation.main import build_app as build_ideation
from ideation.services.agents import Agents
from ideation.settings import IdeationSettings

ROOT = Path(__file__).resolve().parents[2]
AGENT_CASSETTE = ROOT / "eval/behaviour/cassettes/discover_agents.jsonl"


class DiscoverStack(RealStack):
    """``RealStack`` with the real M8 as well."""

    def __init__(self, tmp_path: Path, judge: str = "supported") -> None:
        super().__init__(tmp_path)
        llm = LLMClient(LlmSettings(LLM_BACKEND="fake"), cassette=Cassette.load(AGENT_CASSETTE))
        llm._tracer = self.tracer
        agents = Agents(load_prompts(ROOT / "prompts"), llm, FakeKB(self.kb.cards.values()),
                        self.tracer)  # fmt: skip
        self.real["ideation"] = httpx.ASGITransport(
            build_ideation(IdeationSettings(), agents=agents)
        )
        # one recorded judge reply per claim that passes M12's code rules
        self.judge._entries = [
            {**reply(judge, f"recorded: {judge}"), "agent": "critic"} for _ in range(20)
        ]


async def test_discover_with_the_real_agents_services_and_engine(tmp_path: Path) -> None:
    stack = DiscoverStack(tmp_path)
    m3 = orchestrator(stack)
    run_id = await m3.discover(max_ideas=1)
    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]

    # M8 itself removed the idea that cited only evidence it was not shown
    (generated,) = [i for i in stack.ideas.values() if i.source == "generated"]
    assert generated.title == "Diagram cards"
    (child,) = m3.children(run_id)
    assert m3.state(run_id)["children"][0]["idea"]["gap_evidence_ids"] == [GAP_EVIDENCE]
    (card,) = m3.verdicts(run_id)
    assert card.run_id == child and card.verdict in set(VerdictLabel)
    scored = [s for s in card.scores if s.score is not None]
    assert (
        scored
        and not (await stack.kb.evidence([i for s in scored for i in s.evidence_ids])).missing
    )

    records = await all_records(stack, m3, run_id)
    llm = [r for r in records if r.kind is TraceKind.LLM_CALL]
    assert {r.prompt_version for r in llm} >= {"ideator@v1", "gap_analyst@v2", "skeptic@v1"}
    assert any(r.tool == "search_kb" and r.agent == "gap_analyst" for r in records)
    assert m3.stack.runs[run_id].spend.steps >= 4


def _claim(m3: Orchestrator) -> str:
    return m3.stack.stored[-1].claims[0].claim_id


async def test_challenge_with_the_real_critic_and_engine(tmp_path: Path) -> None:
    stack = DiscoverStack(tmp_path, judge="unsupported")
    m3 = orchestrator(stack)
    run_id = await m3.run()
    before = m3.card(run_id)
    claim = _claim(m3)
    response = await m3.client.post(
        "/ideas/idea_1/challenge", json={"version": 1, "claim_id": claim}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "removed" and body["rationale"]
    after = m3.stack.stored[-1].verdict
    assert after.model_dump(mode="json", by_alias=True) == body["verdict"]
    dimension = Dimension(claim.removeprefix("claim_"))
    old = next(s for s in before.scores if s.dimension is dimension)
    new = next(s for s in after.scores if s.dimension is dimension)
    assert old.score is not None and new.score is None and after.version == 2


async def _verdict_for(tmp_path: Path, **student: Any) -> Any:
    tmp_path.mkdir()  # noqa: ASYNC240  (a test directory)
    m3 = orchestrator(DiscoverStack(tmp_path))
    run_id = await m3.run(**student)
    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    return m3.card(run_id)


async def test_profile_changes_the_verdict(tmp_path: Path) -> None:
    team = await _verdict_for(tmp_path / "team", hours_per_week=10, weeks_available=10, team_size=2)
    solo = await _verdict_for(tmp_path / "solo", hours_per_week=2, weeks_available=10, team_size=1)
    assert team.verdict is not solo.verdict
    assert solo.verdict is VerdictLabel.NO_GO and solo.gates.feasibility is GateResult.FAIL
    assert team.gates.feasibility is GateResult.PASS
