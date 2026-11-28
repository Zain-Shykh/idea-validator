"""The fake stack of M3's tests with the real M9, M12 and M13 behind the same transport, for the
behaviour tests."""

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
from common.contracts.core import TraceRecord
from common.fakes import FakeAnalyst
from common.http import ServiceClient
from common.llm import Cassette, LLMClient
from common.settings import LlmSettings
from common.trace import TraceClient
from critic.main import build_app as build_critic
from critic.settings import CriticSettings
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
