"""VE-16, with the numbers of requirements §7.4 and architecture §13."""

import pytest
from verdict_factories import make_request

from common.contracts.core import Dimension, Goal, Stability, VerdictLabel
from verdict.config import VerdictConfig
from verdict.engine import _weight_map, decide, redistribute, weighted_score


def test_worked_example_verdict(config: VerdictConfig) -> None:
    verdict = decide(make_request(), config)
    assert (verdict.verdict, verdict.stability) == (VerdictLabel.GO, Stability.BORDERLINE)
    assert verdict.score == 70.5
    assert "feasibility weight -20% -> PIVOT (69.9)" in verdict.flips
    # §13 names the feasibility run; two more weight runs also flip it, and all must be listed
    assert verdict.flips == [
        "competition weight +20% -> PIVOT (69.4)",
        "feasibility weight -20% -> PIVOT (69.9)",
        "learning weight -20% -> PIVOT (69.5)",
        "risk score -1 -> PIVOT (68.5)",
    ]
    assert (verdict.sensitivity.runs, verdict.sensitivity.flipped) == (14, 4)


def test_worked_example_feasibility_run_is_69_87(config: VerdictConfig) -> None:
    values = dict(zip(Dimension, (3.5, 2.0, 3.0, 4.0, 3.5, 4.5), strict=True))
    weights = _weight_map(config.weights[Goal.PORTFOLIO])
    assert weighted_score(values, weights) == 70.5
    lowered = redistribute(weights, Dimension.FEASIBILITY, -0.2)
    assert round(weighted_score(values, lowered), 2) == 69.87


def test_card_content_u6(config: VerdictConfig) -> None:
    verdict = decide(make_request(), config)
    assert [s.dimension for s in verdict.scores] == list(Dimension)
    assert all(s.evidence_ids for s in verdict.scores)
    assert verdict.confidence == 0.78  # 0.7 x mean(.75,.7,.65,.75,.6,.7) + 0.3
    assert verdict.weights == config.weights[Goal.PORTFOLIO]
    assert verdict.thresholds == config.thresholds
    assert verdict.weakest_dimension is Dimension.COMPETITION
    assert (verdict.versions.engine, verdict.versions.profile_weights) == ("m11@v1", "w1")
    assert (verdict.versions.rubrics, verdict.versions.kb_version) == ("r1", "kb1")
    assert not verdict.missing and not verdict.gate_details


@pytest.mark.parametrize(
    ("goal", "expected"),
    [("startup", 64.5), ("portfolio", 70.5), ("course_project", 73.0)],
)
def test_weights_come_from_the_goal(config: VerdictConfig, goal: str, expected: float) -> None:
    assert decide(make_request(goal=goal), config).score == expected
