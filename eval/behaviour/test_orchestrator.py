"""M3 against the real M9, M10, M11, M12 and M13 apps, in process (spec Z7, A4 #2, #10, #15).
M8 and the model replies come from cassettes; M2, M4 and M6 are fakes. No real model is called.
"""

import json
from pathlib import Path
from typing import Any

import httpx
from behaviour_stack import AGENTS, EFFORT_CASSETTE, RealStack, all_records, orchestrator

from common.contracts.core import TraceKind, VerdictLabel
from common.contracts.ops import OpsSummary
from common.llm import Cassette
from common.settings import LlmSettings
from scorer.main import build_app as build_scorer_app
from scorer.main import build_scorer
from scorer.settings import ScorerSettings

ROOT = Path(__file__).resolve().parents[2]


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
