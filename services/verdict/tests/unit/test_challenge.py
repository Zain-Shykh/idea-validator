import pytest
from verdict_factories import duplicate, make_request, objection

from common.contracts.core import Dimension, GateResult, Objection, VerdictRequest
from verdict.config import VerdictConfig
from verdict.engine import apply_challenge, decide


@pytest.mark.parametrize(("count", "score", "conf"), [(1, 2.5, 0.6), (2, 2.0, 0.5), (3, 2.0, 0.4)])
def test_penalty_per_objection_with_score_cap(
    config: VerdictConfig, count: int, score: float, conf: float
) -> None:
    request = make_request(
        scores=(3, 3, 3, 3, 3, 3),
        confidences=(0.7,) * 6,
        objections=[objection(n) for n in range(count)],
    )
    effective = apply_challenge(request.scores, request.accepted_objections, config)
    challenged = effective[Dimension.COMPETITION]
    assert (challenged.score, challenged.confidence) == (score, conf)
    assert effective[Dimension.RISK] == request.scores[4]


def test_penalty_clamps_at_zero_and_records_the_original(config: VerdictConfig) -> None:
    request = make_request(
        scores=(3, 0.3, 3, 3, 3, 3),
        confidences=(0.05,) * 6,
        objections=[objection(1), objection(2)],
    )
    challenged = decide(request, config).scores[1]
    assert (challenged.score, challenged.confidence) == (0, 0)
    assert challenged.features["challenge"] == {
        "objection_ids": ["obj_1", "obj_2"],
        "before": {"score": 0.3, "confidence": 0.05},
    }
    assert request.scores[1].score == 0.3  # the request is not modified


def test_unsupported_objection_is_ignored(config: VerdictConfig) -> None:
    request = make_request()
    unsupported = Objection(
        objection_id="o", dimension=Dimension.COMPETITION, text="t", evidence_ids=[]
    )
    effective = apply_challenge(request.scores, [unsupported], config)
    assert list(effective.values()) == request.scores


def test_unsupported_objection_cannot_be_sent_as_accepted() -> None:
    with pytest.raises(ValueError, match="must cite evidence"):
        make_request(objections=[{**objection(1), "evidence_ids": []}])


def test_the_same_objection_counts_once(config: VerdictConfig) -> None:
    request = make_request(objections=[objection(1), objection(1)])
    assert decide(request, config).scores[1].score == 1.5


def test_abstained_dimension_stays_abstained(config: VerdictConfig) -> None:
    request = make_request(scores=(3, None, 3, 3, 3, 3), objections=[objection(1)])
    assert decide(request, config).scores[1].score is None


def test_penalty_is_applied_before_the_duplicate_gate(config: VerdictConfig) -> None:
    request: VerdictRequest = make_request(
        scores=(5, 5, 3.4, 5, 5, 5),
        duplicate=duplicate(),
        objections=[objection(1, "differentiation")],
    )
    assert decide(request, config).gates.duplicate is GateResult.FAIL


def test_penalty_lowers_the_weighted_score(config: VerdictConfig) -> None:
    # VE-16: competition 2.0 -> 1.5 costs 15 x 0.5 / 5 = 1.5 points
    verdict = decide(make_request(objections=[objection(1)]), config)
    assert verdict.score == 69.0
