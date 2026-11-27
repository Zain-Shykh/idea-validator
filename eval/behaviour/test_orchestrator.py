"""M3 against the real M9, M10, M11, M12 and M13 apps, in process (spec Z7, A4 #2, #10, #15).
M8 and the model replies come from cassettes; M2, M4 and M6 are fakes. No real model is called.
"""

import json
from pathlib import Path
from typing import Any

import httpx
from critic_factories import CountingCassette, checker
from orchestrator_factories import Orchestrator, Stack
from validator_factories import NICHES, StubScorer, engine

from agentops.main import build_app as build_agentops
from agentops.migrate import upgrade
from agentops.settings import AgentOpsSettings
from common.contracts.core import TraceKind, TraceRecord, VerdictLabel
from common.contracts.ops import OpsSummary
from common.fakes import FakeAnalyst
from common.http import ServiceClient
from common.llm import Cassette, LLMClient
from common.settings import LlmSettings
from common.trace import TraceClient
from critic.main import build_app as build_critic
from critic.settings import CriticSettings
from scorer.main import build_app as build_scorer_app
from scorer.main import build_scorer
from scorer.settings import ScorerSettings
from validators.main import build_app as build_validators
from validators.settings import ValidatorSettings

ROOT = Path(__file__).resolve().parents[2]
EFFORT_CASSETTE = ROOT / "eval/behaviour/cassettes/validators_effort.jsonl"
AGENTS = json.loads((ROOT / "eval/behaviour/cassettes/orchestrator_agents.json").read_text())


class RealStack(Stack):
    """The fake stack with the real M9, M12 and M13 behind the same transport."""

    def __init__(self, tmp_path: Path, scorer: Any = None) -> None:
        super().__init__()
        url = f"sqlite:///{tmp_path}/ops.db"
        upgrade(url)
        ops = httpx.ASGITransport(
            build_agentops(
                AgentOpsSettings(
                    traces_dir=str(tmp_path / "traces"), database_url=url,
                    rubrics_dir=str(ROOT / "rubrics"), prompts_dir=str(ROOT / "prompts"),
                    config_dir=str(ROOT / "config"),
                )
            )
        )  # fmt: skip
        self.ops = ops
        # every service writes to the same M13
        self.tracer = TraceClient("http://agentops:8000")
        self.tracer._client = ServiceClient("http://agentops:8000", transport=ops)

        effort = LLMClient(LlmSettings(LLM_BACKEND="fake"), cassette=Cassette.load(EFFORT_CASSETTE))
        m9 = engine(
            knowledge=self.kb, analyst=FakeAnalyst(NICHES, []), model=effort,
            scorer=None if scorer is not None else StubScorer(score=4.0),
            transport=scorer,
        )  # fmt: skip
        m9.tracer = effort._tracer = self.tracer
        self.judge = CountingCassette([dict(AGENTS["judge"])])
        critic = checker(knowledge=self.kb, cassette=self.judge)
        critic.tracer = critic.judge.llm._tracer = self.tracer  # type: ignore[union-attr]
        settings = CriticSettings(
            budgets_path=str(ROOT / "config/budgets.yaml"), prompts_dir=str(ROOT / "prompts")
        )
        self.real = {
            "validators": httpx.ASGITransport(build_validators(ValidatorSettings(), engine=m9)),
            "critic": httpx.ASGITransport(build_critic(settings, checker=critic)),
            "agentops": ops,
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls.append((host, request.url.path))
        if host in self.real:
            return await self.real[host].handle_async_request(request)
        self.calls.pop()
        if (host, request.url.path) == ("ideation", "/skeptic"):
            self.calls.append((host, request.url.path))
            evidence = json.loads(request.content)["evidence"][0]["evidence_id"]
            return httpx.Response(
                200,
                json={
                    "objections": [
                        {
                            "objection_id": "o", "dimension": AGENTS["skeptic_dimension"],
                            "text": AGENTS["skeptic_text"], "evidence_ids": [evidence],
                            "fatal": False,
                        }
                    ]
                },
            )  # fmt: skip
        return await super().handle_async_request(request)


async def all_records(stack: RealStack, m3: Orchestrator, run_id: str) -> list[TraceRecord]:
    await stack.tracer.flush()
    await m3.trace.flush()
    async with httpx.AsyncClient(transport=stack.ops, base_url="http://agentops:8000") as api:
        reply = await api.get(f"/runs/{run_id}/trace")
    return [TraceRecord.model_validate(r) for r in reply.json()]


def orchestrator(stack: RealStack) -> Orchestrator:
    """M3's records go to the real M13 like everyone else's."""
    m3 = Orchestrator(stack)
    m3.runner.deps.trace = m3.trace = stack.tracer
    return m3


async def test_validate_with_the_real_services(tmp_path: Path) -> None:
    stack = RealStack(tmp_path)
    stack.questions = AGENTS["intake"]["questions"]
    m3 = orchestrator(stack)
    run_id = await m3.run()
    records = await all_records(stack, m3, run_id)

    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    card = m3.card(run_id)
    scored = [s for s in card.scores if s.score is not None]
    assert scored and all(s.evidence_ids for s in scored)
    cited = [i for s in scored for i in s.evidence_ids]
    assert not (await stack.kb.evidence(cited)).missing
    assert card.verdict in set(VerdictLabel) and (card.score is None) == bool(card.missing)

    agents = {r.agent for r in records}
    assert {"orchestrator", "validators", "critic"} <= agents
    # the judge's cost is on its own record and nowhere else
    (judged,) = [r for r in records if r.agent == "critic" and r.kind is TraceKind.LLM_CALL]
    assert judged.cost_usd == 0.0004
    assert sum(r.cost_usd for r in records) == 0.0004
    assert all(r.cost_usd == 0 for r in records if r.agent == "orchestrator")
    assert stack.judge.calls == 1
    accepted = m3.state(run_id)["verdict_request"]["accepted_objections"]
    assert [o["dimension"] for o in accepted] == [AGENTS["skeptic_dimension"]]

    async with httpx.AsyncClient(transport=stack.ops, base_url="http://agentops:8000") as api:
        summary = OpsSummary.model_validate_json((await api.get("/summary")).text)
    assert (summary.runs_total, summary.verdicts_total) == (1, 1)
    assert summary.verdict_counts[card.verdict] == 1
    assert summary.total_cost_usd == 0.0004
    assert {state.value: n for state, n in summary.runs_by_status.items()} == {"COMPLETED": 1}


async def test_invalid_model_json_abstains_and_the_run_completes(tmp_path: Path) -> None:
    """The real M10 gets only unusable model replies (recorded): every dimension abstains."""
    entries = [
        json.loads(line)
        for line in (ROOT / "eval/behaviour/cassettes/scorer_invalid_json.jsonl")
        .read_text()
        .splitlines()
    ]

    class Always(Cassette):
        def next(self, alias: str, agent: str) -> dict[str, Any]:
            return {"content": entries[0]["content"]}

    scorer_settings = ScorerSettings(
        backend="hosted", rubrics_dir=str(ROOT / "rubrics"), prompts_dir=str(ROOT / "prompts")
    )
    m10 = build_scorer(
        scorer_settings,
        LlmSettings(LLM_BACKEND="fake", LLM_CASSETTE_PATH=str(EFFORT_CASSETTE)),
    )
    assert m10 is not None and m10.hosted is not None
    m10.hosted._cassette = Always([])
    stack = RealStack(
        tmp_path, scorer=httpx.ASGITransport(build_scorer_app(scorer_settings, scorer=m10))
    )
    m10.tracer = m10.hosted._tracer = stack.tracer
    m3 = orchestrator(stack)
    run_id = await m3.run()

    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    card = m3.card(run_id)
    assert card.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE and card.missing
    assert all(s.score is None and s.model == "none" for s in card.scores)
    assert any(s.reason.startswith("scoring failed") for s in card.scores)
    assert not stack.cache


async def test_scorer_timeout_in_m9_abstains_and_the_run_completes(tmp_path: Path) -> None:
    calls: list[str] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadTimeout("slow")

    stack = RealStack(tmp_path, scorer=httpx.MockTransport(timeout))
    m3 = orchestrator(stack)
    run_id = await m3.run()
    assert m3.status(run_id) == "COMPLETED", m3.state(run_id)["error_code"]
    assert m3.card(run_id).verdict is VerdictLabel.INSUFFICIENT_EVIDENCE
    assert len(calls) <= 6 and not stack.cache  # one M10 call a dimension, none repeated
    assert sorted(stack.paths("validators")) == [
        "/score/competition", "/score/demand", "/score/feasibility", "/score/risk",
    ]  # fmt: skip
