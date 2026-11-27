from typing import Any

import pytest
from verdict_factories import duplicate, make_request

from common.contracts.core import GateResult, Stability, Verdict, VerdictLabel
from verdict.config import VerdictConfig
from verdict.engine import decide

IE = VerdictLabel.INSUFFICIENT_EVIDENCE
PASS, FAIL, SKIPPED = GateResult.PASS, GateResult.FAIL, GateResult.NOT_CHECKED
BLOCKER = {
    "type": "legal",
    "description": "scrapes a site that forbids it",
    "evidence_ids": ["card_ab#c0"],
}


def gates(verdict: Verdict) -> tuple[GateResult, ...]:
    g = verdict.gates
    return (g.evidence, g.feasibility, g.risk, g.duplicate)


def assert_gate_decided(verdict: Verdict) -> None:
    assert verdict.score is None and verdict.weakest_dimension is None
    assert verdict.stability is Stability.STABLE
    assert (verdict.sensitivity.runs, verdict.sensitivity.flipped, verdict.flips) == (0, 0, [])


@pytest.mark.parametrize(
    ("sources", "platforms", "said"),
    [(2, 2, "sources: found 2, need at least 3"), (3, 1, "platforms: found 1, need at least 2")],
)
def test_evidence_gate_fails_and_says_what_is_missing(
    config: VerdictConfig, sources: int, platforms: int, said: str
) -> None:
    verdict = decide(make_request(n_sources=sources, n_platforms=platforms), config)
    assert verdict.verdict is IE and gates(verdict) == (FAIL, SKIPPED, SKIPPED, SKIPPED)
    assert any(said in m for m in verdict.missing)
    assert said in verdict.gate_details["evidence"]
    assert verdict.what_would_make_go == []
    assert_gate_decided(verdict)


def test_evidence_gate_passes_with_three_sources_on_two_platforms(config: VerdictConfig) -> None:
    verdict = decide(make_request(n_sources=3, n_platforms=2), config)
    assert verdict.gates.evidence is PASS


@pytest.mark.parametrize(("effort", "hours"), [(192, 240), (12, 15), (0, 0)])
def test_feasibility_gate_passes_at_the_edge(
    config: VerdictConfig, effort: float, hours: float
) -> None:
    verdict = decide(make_request(est_effort_hours=effort, available_hours=hours), config)
    assert verdict.gates.feasibility is PASS and verdict.verdict is VerdictLabel.GO


def test_feasibility_gate_one_hour_over_states_the_gap(config: VerdictConfig) -> None:
    verdict = decide(make_request(est_effort_hours=193, available_hours=240), config)
    assert verdict.verdict is VerdictLabel.NO_GO
    assert gates(verdict) == (PASS, FAIL, SKIPPED, SKIPPED)
    assert "193 h exceeds 192 h" in verdict.gate_details["feasibility"]
    assert "gap 1 h" in verdict.gate_details["feasibility"]
    assert verdict.what_would_make_go == []
    assert_gate_decided(verdict)


def test_unknown_effort_abstains_instead_of_guessing(config: VerdictConfig) -> None:
    verdict = decide(make_request(est_effort_hours=None), config)
    assert verdict.verdict is IE and gates(verdict) == (PASS, SKIPPED, PASS, PASS)
    assert any("build effort" in m for m in verdict.missing)
    assert_gate_decided(verdict)


def test_unknown_effort_does_not_hide_a_blocker(config: VerdictConfig) -> None:
    verdict = decide(make_request(est_effort_hours=None, blockers=[BLOCKER]), config)
    assert verdict.verdict is VerdictLabel.NO_GO
    assert gates(verdict) == (PASS, SKIPPED, FAIL, SKIPPED)


@pytest.mark.parametrize("kind", ["legal", "safety"])
def test_risk_gate_blocker_is_named(config: VerdictConfig, kind: str) -> None:
    verdict = decide(make_request(blockers=[{**BLOCKER, "type": kind}]), config)
    assert verdict.verdict is VerdictLabel.NO_GO
    assert gates(verdict) == (PASS, PASS, FAIL, SKIPPED)
    assert verdict.gate_details["risk"] == f"{kind} blocker: scrapes a site that forbids it"
    assert_gate_decided(verdict)


def test_risk_gate_passes_a_concern_that_is_not_a_blocker(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(5, 5, 5, 5, 1.5, 5)), config)
    assert verdict.gates.risk is PASS and verdict.verdict is VerdictLabel.GO


def test_duplicate_gate_below_override_names_the_product(config: VerdictConfig) -> None:
    request = make_request(scores=(5, 5, 2.9, 5, 5, 5), duplicate=duplicate())
    verdict = decide(request, config)
    assert verdict.verdict is VerdictLabel.NO_GO
    assert gates(verdict) == (PASS, PASS, PASS, FAIL)
    assert "Acme Planner (card_dup)" in verdict.gate_details["duplicate"]
    assert_gate_decided(verdict)


@pytest.mark.parametrize(
    ("differentiation", "candidate"),
    [
        (3.0, duplicate()),
        (0.0, duplicate(same_user=False)),
        (0.0, duplicate(same_core_features=False)),
    ],
)
def test_duplicate_gate_passes(
    config: VerdictConfig, differentiation: float, candidate: dict[str, Any]
) -> None:
    request = make_request(scores=(5, 5, differentiation, 5, 5, 5), duplicate=candidate)
    assert decide(request, config).gates.duplicate is PASS


def test_duplicate_gate_fails_when_differentiation_is_abstained(config: VerdictConfig) -> None:
    request = make_request(scores=(5, 5, None, 5, 5, 5), duplicate=duplicate())
    verdict = decide(request, config)
    assert verdict.gates.duplicate is FAIL
    assert "not scored" in verdict.gate_details["duplicate"]
    assert verdict.what_would_make_go == []


@pytest.mark.parametrize(
    ("inputs", "expected", "label"),
    [
        ({"n_sources": 0, "est_effort_hours": 999}, (FAIL, SKIPPED, SKIPPED, SKIPPED), IE),
        ({"est_effort_hours": 999}, (PASS, FAIL, SKIPPED, SKIPPED), VerdictLabel.NO_GO),
        ({}, (PASS, PASS, FAIL, SKIPPED), VerdictLabel.NO_GO),
    ],
)
def test_first_failing_gate_decides(
    config: VerdictConfig,
    inputs: dict[str, Any],
    expected: tuple[GateResult, ...],
    label: VerdictLabel,
) -> None:
    # everything that can fail does fail; the earliest wins
    request = make_request(
        scores=(5, 5, 0, 5, 5, 5), blockers=[BLOCKER], duplicate=duplicate(), **inputs
    )
    verdict = decide(request, config)
    assert (gates(verdict), verdict.verdict) == (expected, label)
