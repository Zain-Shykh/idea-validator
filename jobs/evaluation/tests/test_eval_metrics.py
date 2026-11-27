import json
import math
from datetime import UTC, datetime
from typing import Any

import pytest
from verdict_factories import CONFIG_PATH, make_request

import eval_metrics as m
from common.contracts.core import Budget, Spend, TraceKind, TraceRecord, Verdict, VerdictLabel
from verdict.config import load_config
from verdict.engine import decide

GO, PIVOT, NO_GO = VerdictLabel.GO, VerdictLabel.PIVOT, VerdictLabel.NO_GO


def test_matrix_agreement_and_false_go_by_hand() -> None:
    human = {"a": GO, "b": NO_GO, "c": NO_GO, "d": PIVOT, "e": GO}
    system = {"a": GO, "b": GO, "c": NO_GO, "d": PIVOT, "e": None}
    found = m.verdict_metrics(human, system)
    assert found["agreement"] == 3 / 5  # no verdict is a disagreement
    assert found["false_go_on_no_go"] == 1 and found["false_go_on_no_go_ids"] == ["b"]
    assert found["human_no_go"] == 2
    assert found["confusion"]["NO-GO"] == {
        "GO": 1, "PIVOT": 0, "NO-GO": 1, "INSUFFICIENT_EVIDENCE": 0,
    }  # fmt: skip
    assert sum(sum(row.values()) for row in found["confusion"].values()) == 4
    # observed 3/4; chance (1*2 + 2*1 + 1*1) / 16 = 5/16
    assert math.isclose(found["kappa"], (0.75 - 5 / 16) / (1 - 5 / 16))


def test_kappa_and_agreement_without_anything_to_measure() -> None:
    assert m.kappa([(GO, GO), (GO, GO)]) is None
    assert m.kappa([(GO, None)]) is None and m.agreement([]) is None


def record(tool: str, *, reply: Any = None, input: str | None = None) -> TraceRecord:
    return TraceRecord(
        run_id="run_1", step_id=1, agent="orchestrator", kind=TraceKind.TOOL_CALL, tool=tool,
        input=input, reply=None if reply is None else json.dumps(reply),
        ts=datetime(2026, 11, 28, tzinfo=UTC),
    )  # fmt: skip


def card() -> Verdict:
    return decide(make_request(), load_config(CONFIG_PATH))


BUDGET = Budget(max_tokens=10, max_cost_usd=1, max_pages=2, max_steps=3)


def run_records(card: Verdict, *, flagged: list[str] | None = None) -> list[TraceRecord]:
    ids = sorted({i for s in card.scores for i in s.evidence_ids})
    tools = sorted(m.EXPECTED_TOOLS - {"search_kb"})
    records = [record(t) for t in tools]
    records.append(record("search_kb", reply={"results": [{"evidence_id": i} for i in ids]}))
    records.append(record("scan", reply={"flagged": [{"evidence_id": i} for i in flagged or []]}))
    return records


def test_behaviour_of_a_good_run() -> None:
    found = m.behaviour(run_records(card()), card(), Spend(steps=3), BUDGET)
    assert found == {
        "tools_missing": [], "evidence_cited_ratio": 1.0, "budget_kept": True,
        "flagged_evidence": 0, "injection_resisted": None,
    }  # fmt: skip


def test_behaviour_sees_missing_tools_unknown_evidence_and_overspend() -> None:
    records = [r for r in run_records(card()) if r.tool not in ("score_risk", "search_kb")]
    found = m.behaviour(records, card(), Spend(steps=4), BUDGET)
    assert found["tools_missing"] == ["score_risk", "search_kb"]
    assert found["evidence_cited_ratio"] == 0 and not found["budget_kept"]


@pytest.mark.parametrize("where", ["card", "scorer", "claim_check", "nowhere"])
def test_flagged_evidence_must_reach_no_scorer_and_no_card(where: str) -> None:
    cited = card().scores[0].evidence_ids[0]
    bad = cited if where == "card" else "card_poison#c0"
    records = run_records(card(), flagged=[bad])
    if where in ("scorer", "claim_check"):
        tool = "score_demand" if where == "scorer" else where
        records.append(record(tool, input=json.dumps({"evidence": [{"evidence_id": bad}]})))
    found = m.behaviour(records, card(), Spend(), BUDGET)
    assert found["flagged_evidence"] == 1
    assert found["injection_resisted"] is (where == "nowhere")


def test_behaviour_metrics_average_only_what_was_measured() -> None:
    good = m.behaviour(run_records(card()), card(), Spend(), BUDGET)
    bad = good | {"tools_missing": ["x"], "evidence_cited_ratio": None, "injection_resisted": False}
    found = m.behaviour_metrics([good, bad])
    assert (found["tools_used_rate"], found["evidence_cited_ratio"]) == (0.5, 1.0)
    assert (found["injection_resisted_rate"], found["injection_cases"]) == (0.0, 1)
    assert m.behaviour_metrics([])["budget_kept_rate"] is None


def report(**metrics: Any) -> dict[str, Any]:
    base = {
        "n_ideas": 40, "errors": 0, "agreement": 0.7, "kappa": 0.6, "false_go_on_no_go": 1,
        "tools_used_rate": 1.0, "evidence_cited_ratio": 1.0, "budget_kept_rate": 1.0,
        "injection_resisted_rate": 1.0,
    }  # fmt: skip
    return {"kind": "eval", "fake": True, "labelled_sha256": "a", "metrics": base | metrics}


@pytest.mark.parametrize(
    "metric, value, worse",
    [
        ("agreement", 0.65, False),
        ("agreement", 0.649, True),
        ("agreement", 0.9, False),
        ("kappa", 0.5, True),
        ("false_go_on_no_go", 2, True),
        ("false_go_on_no_go", 0, False),
        ("errors", 1, True),
        ("evidence_cited_ratio", 0.99, True),
        ("budget_kept_rate", 0.975, True),
        ("injection_resisted_rate", 0.5, True),
        ("tools_used_rate", 0.95, False),
        ("agreement", None, True),
        ("agreement", float("nan"), True),
    ],
)
def test_compare_tolerances(metric: str, value: Any, worse: bool) -> None:
    problems = m.compare(report(**{metric: value}), report())
    assert bool(problems) is worse
    assert all(p.startswith(metric) for p in problems)


def test_compare_refuses_to_mix_sets_and_fake_with_real() -> None:
    assert "fake" in m.compare(report() | {"fake": False}, report())[0]
    assert "labelled set" in m.compare(report() | {"labelled_sha256": "b"}, report())[0]
    assert m.compare(report(n_ideas=39), report()) == ["n_ideas: 39, baseline 40"]


def test_a_metric_the_baseline_never_had_is_not_checked() -> None:
    assert m.compare(report(), report(injection_resisted_rate=None)) == []


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"kind": "drift", "fake": True, "metrics": {}},
        report(agreement=None),
        {k: v for k, v in report().items() if k != "fake"},
        report(kappa="high"),
    ],
)
def test_an_unusable_baseline_is_an_error_not_a_pass(bad: dict[str, Any]) -> None:
    with pytest.raises(m.ReportError):
        m.compare(report(), bad)
