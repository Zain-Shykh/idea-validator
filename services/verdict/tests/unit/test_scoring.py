import pytest
from verdict_factories import make_request

from common.contracts.core import Dimension, VerdictLabel
from verdict.config import VerdictConfig
from verdict.engine import apply_challenge, confidence, decide

IE = VerdictLabel.INSUFFICIENT_EVIDENCE


def flat(score: float) -> tuple[float, ...]:
    return (score,) * 6


@pytest.mark.parametrize(
    ("score100", "label"),
    [
        (70.0, VerdictLabel.GO),
        (69.99, VerdictLabel.PIVOT),
        (50.0, VerdictLabel.PIVOT),
        (49.99, VerdictLabel.NO_GO),
    ],
)
@pytest.mark.parametrize("goal", ["startup", "portfolio", "course_project"])
def test_threshold_edges(
    config: VerdictConfig, goal: str, score100: float, label: VerdictLabel
) -> None:
    verdict = decide(make_request(goal=goal, scores=flat(score100 / 20)), config)
    assert (verdict.score, verdict.verdict) == (score100, label)


def test_score_too_low_is_said(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=flat(2.0)), config)
    assert verdict.gate_details["score"] == "score too low: 40.00 is below 50"


@pytest.mark.parametrize("index", range(5))
def test_floor_breach_turns_a_high_score_into_pivot(config: VerdictConfig, index: int) -> None:
    scores = [5.0] * 6
    scores[index] = 1.49
    verdict = decide(make_request(scores=tuple(scores)), config)
    breached = list(Dimension)[index]
    assert verdict.verdict is VerdictLabel.PIVOT
    assert verdict.score is not None and verdict.score >= 70
    assert verdict.weakest_dimension is breached
    assert breached.value.replace("_", " ") + " 1.49" in verdict.gate_details["floor"]


def test_floor_value_itself_is_not_a_breach(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(1.5, 5, 5, 5, 5, 5)), config)
    assert verdict.verdict is VerdictLabel.GO and "floor" not in verdict.gate_details


def test_floor_does_not_apply_to_learning(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(5, 5, 5, 5, 5, 0)), config)
    assert (verdict.verdict, verdict.score) == (VerdictLabel.GO, 80.0)


def test_floor_breach_below_t_no_is_no_go(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(1, 2, 2, 2, 2, 2)), config)
    assert verdict.verdict is VerdictLabel.NO_GO


@pytest.mark.parametrize(
    ("scores", "weakest"),
    [
        # feasibility (25) outweighs demand (15)
        ((2, 4, 4, 2, 4, 4), Dimension.FEASIBILITY),
        # equal score and weight: declaration order
        ((2, 2, 4, 4, 4, 4), Dimension.DEMAND_PAIN),
        ((4, 4, 4, 4, 4, 1), Dimension.LEARNING),
    ],
)
def test_weakest_dimension_ties(
    config: VerdictConfig, scores: tuple[float, ...], weakest: Dimension
) -> None:
    assert decide(make_request(scores=scores), config).weakest_dimension is weakest


@pytest.mark.parametrize(
    ("each", "sources", "expected"),
    [(0.5, 5, 0.65), (0.5, 50, 0.65), (0.5, 3, 0.53), (1.0, 0, 0.7)],
)
def test_confidence_formula_caps_sources_at_five(
    config: VerdictConfig, each: float, sources: int, expected: float
) -> None:
    request = make_request(confidences=(each,) * 6)
    effective = apply_challenge(request.scores, [], config)
    assert confidence(effective, sources, config) == pytest.approx(expected)


@pytest.mark.parametrize(("target", "abstains"), [(0.5, False), (0.4999, True)])
def test_confidence_threshold_edge(config: VerdictConfig, target: float, abstains: bool) -> None:
    # 3 sources contribute 0.18, the mean of the six confidences has to give the rest
    each = (target - 0.18) / 0.7
    verdict = decide(make_request(confidences=(each,) * 6, n_sources=3), config)
    assert (verdict.verdict is IE) is abstains
    if abstains:
        assert verdict.score is None and verdict.sensitivity.runs == 0
        assert "confidence too low: 0.4999 is below 0.5" in verdict.missing
        assert verdict.what_would_make_go == []


def test_abstained_dimension_counts_as_zero_and_is_listed(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(5, 5, 5, 5, 5, None), confidences=(0.9,) * 6), config)
    assert (verdict.verdict, verdict.score) == (VerdictLabel.GO, 80.0)
    assert verdict.missing == ["an evidence-backed score for learning"]
    assert verdict.scores[-1].score is None and verdict.scores[-1].confidence == 0


def test_abstained_floor_dimension_blocks_go(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(None, 5, 5, 5, 5, 5), confidences=(0.9,) * 6), config)
    assert verdict.verdict is VerdictLabel.PIVOT and "floor" in verdict.gate_details


def test_all_abstained_is_insufficient_evidence(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(None,) * 6), config)
    assert verdict.verdict is IE and len(verdict.missing) == 7  # six scores + low confidence
