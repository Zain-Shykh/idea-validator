import math

import pytest
from verdict_factories import make_request

from common.contracts.core import Dimension, Goal, Stability, VerdictLabel
from verdict.config import VerdictConfig
from verdict.engine import _weight_map, decide, redistribute, sensitivity


@pytest.mark.parametrize("goal", list(Goal))
@pytest.mark.parametrize("delta", [0.2, -0.2])
@pytest.mark.parametrize("dimension", list(Dimension))
def test_redistributed_weights_sum_to_100_in_proportion(
    config: VerdictConfig, goal: Goal, delta: float, dimension: Dimension
) -> None:
    before = _weight_map(config.weights[goal])
    after = redistribute(before, dimension, delta)
    assert math.isclose(math.fsum(after.values()), 100, rel_tol=0, abs_tol=1e-9)
    assert after[dimension] == pytest.approx(before[dimension] * (1 + delta))
    ratios = {round(after[d] / before[d], 9) for d in Dimension if d != dimension}
    assert len(ratios) == 1


def test_redistribution_matches_the_worked_example(config: VerdictConfig) -> None:
    weights = _weight_map(config.weights[Goal.PORTFOLIO])
    after = redistribute(weights, Dimension.FEASIBILITY, -0.2)
    assert [round(w, 2) for w in after.values()] == [16, 16, 16, 20, 10.67, 21.33]


@pytest.mark.parametrize(
    ("score", "label"), [(5, VerdictLabel.GO), (3, VerdictLabel.PIVOT), (1, VerdictLabel.NO_GO)]
)
def test_exactly_14_runs_for_score_decided_verdicts(
    config: VerdictConfig, score: float, label: VerdictLabel
) -> None:
    request = make_request(scores=(score,) * 6)
    verdict = decide(request, config)
    assert (verdict.verdict, verdict.sensitivity.runs) == (label, 14)
    values = {s.dimension: float(score) for s in request.scores}
    weights = _weight_map(config.weights[Goal.PORTFOLIO])
    assert sensitivity(values, values, weights, label, config)[0] == 14


def test_stable_case(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(4.5,) * 6), config)
    assert verdict.stability is Stability.STABLE
    assert (verdict.sensitivity.flipped, verdict.flips) == (0, [])
    assert verdict.what_would_make_go == []  # nothing to do for a stable GO


def test_score_run_moves_only_the_least_confident_dimension(config: VerdictConfig) -> None:
    # 70.5 base: -1 on feasibility (weight 25) gives 65.5
    confidences = (0.8, 0.8, 0.8, 0.5, 0.8, 0.8)
    verdict = decide(make_request(confidences=confidences), config)
    score_flips = [f for f in verdict.flips if " score " in f]
    assert score_flips == ["feasibility score -1 -> PIVOT (65.5)"]


def test_least_confident_tie_takes_the_higher_weight(config: VerdictConfig) -> None:
    verdict = decide(make_request(confidences=(0.8,) * 6), config)
    assert [f for f in verdict.flips if " score " in f] == ["feasibility score -1 -> PIVOT (65.5)"]


@pytest.mark.parametrize(("base", "moved"), [(0.5, "+1"), (4.8, "-1")])
def test_score_run_is_clamped(config: VerdictConfig, base: float, moved: str) -> None:
    values = dict.fromkeys(Dimension, base)
    weights = _weight_map(config.weights[Goal.PORTFOLIO])
    # compare with an impossible label so every run is reported with its score
    _, lines = sensitivity(values, values, weights, VerdictLabel.INSUFFICIENT_EVIDENCE, config)
    assert len(lines) == 14
    runs = {line.split(" ->")[0]: float(line.rsplit("(", 1)[1][:-1]) for line in lines}
    changed = max(0.0, base - 1) if moved == "-1" else min(5.0, base + 1)
    expected = round((base * 75 + changed * 25) / 5, 1)  # feasibility wins the tie
    assert runs[f"feasibility score {moved}"] == expected
